# Copyright The IETF Trust 2023, All Rights Reserved
# -*- coding: utf-8 -*-

from collections import defaultdict, namedtuple
import datetime
from itertools import cycle, pairwise
import rpcapi_client

from tqdm import tqdm

from django.core.management.base import BaseCommand

from datatracker.models import DatatrackerPerson, Document
from datatracker.rpcapi import with_rpcapi

from rfced.models import (
    Clusters,
    EditorAssignments,
    Editors,
    Index,
    StateHistory,
    States,
    WorkingGroup,
)

from ...models import (
    AdditionalEmail,
    Assignment,
    Cluster,
    ClusterMember,
    HistoricalRfcToBe,  # type: ignore (managed by django-simple-history)
    Label,
    RfcAuthor,
    RfcToBe,
    RpcPerson,
    SourceFormatName,
    StdLevelName,
    StreamName,
    TAILWIND_COLORS,
)

from ...utils_xfer import TransformHelper, RfcMatchHelper, DraftMatchHelper

# Arcana
IN_PROGRESS_STATES = [1, 2, 4, 10, 12, 13, 15, 17, 18, 22, 23, 20]
PUBLISHED_STATES = [14]

# Special label names
IANA_FLAG_LABEL_NAME: str = "*A"
REF_FLAG_LABEL_NAME: str = "*R"


@with_rpcapi
def get_rfc_original_streams(*, rpcapi: rpcapi_client.DefaultApi):
    original_stream_list = rpcapi.get_rfc_original_streams().original_stream
    original_streams = dict()
    for item in original_stream_list:
        original_streams[item.rfc_number] = item.stream
    return original_streams


@with_rpcapi
def person_by_id(id, *, rpcapi: rpcapi_client.DefaultApi):
    try:
        return rpcapi.get_person_by_id(id)
    except rpcapi_client.ApiException as e:
        if e.status == 404:
            return None
        else:
            raise e


@with_rpcapi
def persons_by_id(ids, *, rpcapi: rpcapi_client.DefaultApi):
    return rpcapi.get_persons(ids)


@with_rpcapi
def persons_by_email(emails, *, rpcapi: rpcapi_client.DefaultApi):
    result = dict()
    for item in rpcapi.persons_by_email(emails):
        result[item.email] = item
    return result


@with_rpcapi
def rfc_authors(rfc_numbers, *, rpcapi: rpcapi_client.DefaultApi):
    return rpcapi.get_rfc_authors(rfc_numbers)


@with_rpcapi
def draft_authors(draft_names, *, rpcapi: rpcapi_client.DefaultApi):
    return rpcapi.get_draft_authors(draft_names)


@with_rpcapi
def update_documents(docnames, *, rpcapi: rpcapi_client.DefaultApi):
    documents = rpcapi.get_drafts_by_names(docnames)
    for name in tqdm(docnames, desc="update_documents", disable=None):
        if name not in documents:
            print(f"skipping create_or_update of {name}")
            continue
        docinfo = documents[name]
        doc, created = Document.objects.get_or_create(
            datatracker_id=docinfo["id"],
            defaults={
                "name": docinfo["name"],
                "rev": docinfo["rev"],
                "title": docinfo["title"],
                "stream": docinfo["stream"],
                "pages": docinfo["pages"],
            },
        )
        if not created:
            # Update the record with latest information
            assert doc.name == docinfo["name"]
            doc.rev = docinfo["rev"]
            doc.title = docinfo["title"]
            doc.stream = docinfo["stream"]
            doc.pages = docinfo["pages"]
            doc.save()

    return documents


class Command(BaseCommand):
    help = "Transfer data from a dump of the current RPC production database"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.people_pks = {
            # Direct name matches in datatracker
            "Aaron Falk": 21226,
            "Sarah Tarrant": 131267,
            "Jean Mahoney": 114455,
            "Aaron Stone": 110093,
            "Sandy Ginoza": 104401,
            "Alice Russo": 113811,
            "Marshika Szabo": 127045,
            "Karen Moore": 127686,
            "Megan Ferguson": 128027,
            "Lynne Bartholomew": 128311,
            "Rebecca VanRheenen": 130328,
            "Alanna Paloma": 130334,
            "Chris Smiley": 130367,
            "Reuben Esparza": 131485,
            "Madison Church": 134025,
            # Found by manual searching
            "Bob Braden": 5436,  # "Robert Braden"
            "Jon Postel": 419,  # "Dr. Jon Postel"
            "Joyce Reynolds": 2804,  # "Joyce K. Reynolds"
            "Alice Hagens": 113811,  # "Alice Russo"
            # There are 22 "Names" from the rfced Editors table not yet found in the datatracker
        }

        self.index_id_of = dict()

        self.draftinfo = dict()

        self.label_name_to_id: dict[str, int] = dict()

        SourceFormatName.objects.get_or_create(
            slug="nroff",
            name="nroff",
            desc="Source was submitted in nroff",
            used=False,  # THIS was why we needed to add used...
        )

    def handle(self, *args, **options):
        self.validate_persons()
        self.import_labels()
        self.build_rpc_people()
        self.get_published_rfcs()
        self.get_in_process_docs()
        self.get_assignments()
        self.import_clusters()
        self.get_authors_and_approvers()

        # TODO

        # Get withdrawn docs

        # Account for "not issued" index rows (did any have any work to capture?)

        # Handle other document types (?)
        #   >>> Counter(Index.objects.values_list('type',flat=True))
        #   Counter({'RFC': 9775, 'IEN': 208, 'BCP': 31, 'STD': 27})
        # Maybe make a Subseries model.

        # END TODO

    def validate_persons(self):
        # Check that all persons in the RfcMatchHelper list are returned by rpc-api
        missing_persons = []
        rfc_match_helper = RfcMatchHelper()
        person_ids = list(
            map(lambda item: item[0][1], rfc_match_helper.MATCHES.items())
        )
        rpcapi_persons = persons_by_id(person_ids)

        for person_id in tqdm(person_ids, desc="validate_persons", disable=None):
            if str(person_id) not in rpcapi_persons:
                missing_persons.append(person_id)

        if missing_persons:
            print(f"Missing persons: {missing_persons}")
            raise CommandError(
                "Script aborted! Please check the rpc-api for missing persons."
            )

    def update_draftinfo(self, docnames):
        updoc = update_documents(docnames)
        if updoc is not None:
            self.draftinfo.update(updoc)

    def import_labels(self):
        # Build id-name pairs from states
        label_id_name_pairs: list[tuple[int, str]] = [
            (row[0], row[1])
            for row in States.objects.all().values_list("state_id", "state_name")
        ]

        # Import missing state (state_id: 0) as unknown
        assert 0 not in [i for (i, _) in label_id_name_pairs]
        label_id_name_pairs.append((0, "unknown"))  # state 0

        # Import *A and *R flags as separate labels
        max_id: int = max(i for (i, _) in label_id_name_pairs)
        flags: list[str] = [IANA_FLAG_LABEL_NAME, REF_FLAG_LABEL_NAME]
        for offset, flag in enumerate(flags, start=1):
            label_id_name_pairs.append((max_id + offset, flag))

        # Import labels
        for (i, n), c in zip(
            tqdm(label_id_name_pairs, desc="import_labels", disable=None),
            cycle(TAILWIND_COLORS),
        ):
            Label.objects.get_or_create(id=i, slug=n, color=c)
            self.label_name_to_id[n] = i

    def build_rpc_people(self):
        for name in self.people_pks:
            datatracker_person, _ = DatatrackerPerson.objects.get_or_create(
                datatracker_id=self.people_pks[name]
            )
            rpc_person, _ = RpcPerson.objects.get_or_create(
                datatracker_person=datatracker_person,
                defaults={
                    # TODO: right now leave all these to the database defaults.
                },
            )

    def get_published_rfcs(self):
        RFCS_WITH_BROKEN_DRAFTNAMES = {
            "RFC2605": "draft-ietf-madman-dsa-mib-1 : 2024-08-30 missing the version number, should be draft-ietf-madman-dsa-mib-1-10",
            "RFC3018": "draft-bogdanov-umsp : 2024-08-30 potaroo.net knows about -00 and -01 versions, but datatracker does not",
        }
        RFCS_WITH_FAKE_DRAFTNAMES = {
            "RFC5540": "draft-rfc-editor-40-anniversary-00 was developed by rfc-editor without submitting a draft",
            "RFC6019": 'rfc4049bis : 2010-08-18   RFC 4049 was published in April 2005 as an Experimental RFC.  The IESG states that "The document is already referenced normatively by some existing Standard Track RFCs, documents approved for publication and draft.  For all practical purposes this document is a already a Standards Track document."  There are several changes to be made to the Proposed-Standard document, as indicated in the RFC Editor Notes of the 2010-08-16 IESG approval email.  The 2005 document does not have an IANA Considerations section."',
            "RFC6342": "draft-ietf-v6ops-v6-in-mobile-networks-rfc6312bis : Draft string is fake; added to be a republication of RFC 6312.  RFC Editor made a mistake in publishing RFC 6312 with the number 6212 contained in it!  This doc corrects that error.",
            "RFC6353": "draft-ietf-isms-dtls-tm-rfc5953bis-00 : I-D string is fake; created it to upgrade rfc5953 to Draft Standard after incorporating changes requested in RFC Editor notes.",
            "RFC7159": "draft-ietf-json-rfc4627bis-rfc7159bis : 2014-03-03: This doc is a repub of RFC 7158 because we had the wrong year in the date field (it was marked 2013). This RFC corrects that error",
            "RFC7396": "draft-ietf-rfc7386bis-00 : 2014-10-27: This doc is not a real I-D. This is a republication of RFC 7386 because of a formatting error caused by the RFC Editor (either manual insertion of tabs into artwork or insertion of tabs into artwork by the text editor being used). Paul corrected the formatting errors and send us the .xml file; it was not posted as an I-D because it was a formatting error only.",
        }
        rfc_qs = Index.objects.filter(
            type="RFC", state_id__in=PUBLISHED_STATES
        ).exclude(status="NOT ISSUED")
        names = (
            rfc_qs.exclude(draft__isnull=True)
            .exclude(draft="")
            .exclude(pub_date__month=4, pub_date__day=1)
            .exclude(doc_id__in=RFCS_WITH_FAKE_DRAFTNAMES.keys())
            .exclude(doc_id__in=RFCS_WITH_BROKEN_DRAFTNAMES.keys())
            .values_list("draft", flat=True)
        )
        # All remaining draft names include version numbers - strip them
        self.update_draftinfo([name.strip()[:-3] for name in names])
        # Fixup what we can fixup
        self.update_draftinfo(
            [
                "draft-ietf-madman-dsa-mib-1",
            ]
        )
        original_streams = get_rfc_original_streams()

        # Cache labels in memory for faster lookups
        labels = Label.objects.all()
        labels_cached: dict[int, Label] = {label.id: label for label in labels}

        for row in tqdm(rfc_qs, desc="get_published_rfcs", disable=None):
            is_apr1 = (
                row.pub_date and row.pub_date.month == 4 and row.pub_date.day == 1
            ) or False
            found_doc = None
            if (
                not row.doc_id in RFCS_WITH_FAKE_DRAFTNAMES.keys()
                and row.doc_id != "RFC3018"  # semi-broken in datatracker
                and not is_apr1
                and row.draft is not None
                and row.draft != ""
            ):
                name = row.draft.strip()
                if name == "draft-ietf-madman-dsa-mib-1":
                    name = (
                        "draft-ietf-madman-dsa-mib-1-10"  # repair broken entry in index
                    )
                name = name[:-3]
                found_doc = Document.objects.filter(name=name).first()
                if not found_doc:
                    print(f"Skipping {row.doc_id} - problem with {row.draft}")
                    continue
            rfc_number = int(row.doc_id[3:])
            rfc_to_be = RfcToBe.objects.create(
                disposition_id="published",
                is_april_first_rfc=is_apr1,
                draft=found_doc,
                rfc_number=rfc_number,
                submitted_format_id=self.source_format_id_from_index(row),
                submitted_std_level=StdLevelName.objects.from_slug(
                    self.dt_stdlevelname_slug(row.pub_status)
                ),  # Not sure this is right - may need to go find last version of draft instead?
                submitted_boilerplate_id="unknown",
                submitted_stream=StreamName.objects.from_slug(
                    found_doc.stream if found_doc else "ise"
                ),
                intended_std_level=StdLevelName.objects.from_slug(
                    self.dt_stdlevelname_slug(row.status)
                ),  # Again not sure this is right - current status may belong to RFC in datatracker
                intended_boilerplate_id="unknown",
                intended_stream=StreamName.objects.from_slug(
                    original_streams[rfc_number]
                ),
                external_deadline=None,  # TODO - capture known ones?
                internal_goal=None,  # TODO - does the rfced db capture this?
            )
            self.index_id_of[rfc_to_be] = row.pk
            system, _ = DatatrackerPerson.objects.get_or_create(datatracker_id=1)
            if row.doc_id in RFCS_WITH_FAKE_DRAFTNAMES:
                rfc_to_be.rpcdocumentcomment_set.create(
                    comment=RFCS_WITH_FAKE_DRAFTNAMES[row.doc_id], by=system
                )
            elif row.doc_id in RFCS_WITH_BROKEN_DRAFTNAMES:
                rfc_to_be.rpcdocumentcomment_set.create(
                    comment=RFCS_WITH_BROKEN_DRAFTNAMES[row.doc_id], by=system
                )
            elif found_doc is None and not is_apr1:
                rfc_to_be.rpcdocumentcomment_set.create(
                    comment="No draft available for this older RFC", by=system
                )

            # walk states and apply labels (with history)
            state_history = StateHistory.objects.filter(
                internal_dockey=row.internal_key
            ).order_by(
                "in_date",  # type: date (not datetime)
                "id",  # rely on autoincrement to sort multiple states from same day
            )

            label_history: list[set[int]] = []
            label_dates: list[datetime.date] = []
            for sh in state_history:
                label_ids: list[int] = []
                label_ids.append(sh.state_id)
                if sh.iana_flag:
                    label_ids.append(self.label_name_to_id[IANA_FLAG_LABEL_NAME])
                if sh.ref_flag:
                    label_ids.append(self.label_name_to_id[REF_FLAG_LABEL_NAME])
                label_history.append(set(label_ids))
                label_dates.append(sh.in_date)

            assert len(label_history) == len(label_dates)
            if not label_history:
                continue  # TODO: refactor this so other unrelated code can follow in the loop.

            def _update_latest_history_date(
                history_date: datetime.date,
            ) -> HistoricalRfcToBe:
                h: HistoricalRfcToBe = rfc_to_be.history.latest()
                h.history_date = datetime.datetime(
                    history_date.year,
                    history_date.month,
                    history_date.day,
                    tzinfo=datetime.timezone.utc,
                )
                return h

            history: list[HistoricalRfcToBe] = []

            # override date of doc's first change (+)
            # TODO: this isn't right - the concept of history needs to be refactored
            # to be accounted at the actual RfcToBe creation and any other changes need
            # to be reflected in that history.
            history.append(_update_latest_history_date(min(label_dates)))

            # first set of labels (always additions since doc has no labels initially)
            for label_id in sorted(label_history[0]):
                rfc_to_be.labels.add(labels_cached[label_id])
                history.append(_update_latest_history_date(label_dates[0]))
            # subsequent sets of labels (subtractions and/or additions)
            for (old, _), (new, date) in pairwise(zip(label_history, label_dates)):
                # if no intersection, clear existing labels and then add all new labels at once
                intersection: set[int] = old & new
                if not intersection:
                    rfc_to_be.labels.set([])
                    history.append(_update_latest_history_date(date))
                    rfc_to_be.labels.set([labels_cached[id_] for id_ in sorted(new)])
                    history.append(_update_latest_history_date(date))
                    continue
                # otherwise, add and remove labels one by one
                removed: set[int] = old - new
                added: set[int] = new - old
                for label_id in sorted(removed):
                    rfc_to_be.labels.remove(labels_cached[label_id])
                    history.append(_update_latest_history_date(date))
                for label_id in sorted(added):
                    rfc_to_be.labels.add(labels_cached[label_id])
                    history.append(_update_latest_history_date(date))

            # bulk update on HistoricalRfcToBe.history_date
            HistoricalRfcToBe.objects.bulk_update(history, ["history_date"])

    def get_in_process_docs(self):
        ip_qs = Index.objects.filter(type="RFC", state_id__in=IN_PROGRESS_STATES)
        names = (
            ip_qs.exclude(draft__isnull=True)
            .exclude(draft="")
            .exclude(pub_date__month=4, pub_date__day=1)
            .values_list("draft", flat=True)
        )
        self.update_draftinfo([name.strip()[:-3] for name in names])

        # Cache labels in memory for faster lookups
        labels = Label.objects.all()
        labels_cached: dict[int, Label] = {label.id: label for label in labels}

        for row in tqdm(ip_qs, desc="get_in_process_docs", disable=None):
            is_apr1 = (
                row.pub_date and row.pub_date.month == 4 and row.pub_date.day == 1
            ) or False
            found_doc = None
            if not is_apr1 and row.draft is not None and row.draft != "":
                found_doc = Document.objects.filter(name=row.draft.strip()[:-3]).first()
                if not found_doc:
                    print(f"Skipping {row.doc_id} - problem with {row.draft}")
                    continue
            # Extract the rfc_number if we can
            if row.doc_id is None or row.doc_id == "RFC":
                maybe_rfc_number = None
            else:
                if not row.doc_id.startswith("RFC"):
                    print(f"WARNING: DOC-ID '{row.doc_id}' for {row.draft} does not start with 'RFC'")
                maybe_rfc_number = int(row.doc_id[3:])
            rfc_to_be = RfcToBe.objects.create(
                disposition_id="in_progress",
                is_april_first_rfc=is_apr1,
                draft=found_doc,
                rfc_number=maybe_rfc_number,
                submitted_format_id=self.source_format_id_from_index(row),
                submitted_std_level=StdLevelName.objects.from_slug(
                    self.dt_stdlevelname_slug(row.pub_status)
                ),  # Not sure this is right - may need to go find last version of draft instead?
                submitted_boilerplate_id="unknown",
                submitted_stream=StreamName.objects.from_slug(
                    found_doc.stream if found_doc else "ise"
                ),
                intended_std_level=StdLevelName.objects.from_slug(
                    self.dt_stdlevelname_slug(row.status)
                ),  # Closer to sure this is right
                intended_boilerplate_id="unknown",
                intended_stream=StreamName.objects.from_slug(
                    # this is the stream as of the time of the import run
                    # which makes sense for documents that are in progress
                    self.stream_slug_from_index(row)
                ),
                external_deadline=None,  # TODO - capture known ones?
                internal_goal=None,  # TODO - does the rfced db capture this?
            )
            self.index_id_of[rfc_to_be] = row.pk

            # walk states and apply labels (with history)
            state_history = StateHistory.objects.filter(
                internal_dockey=row.internal_key
            ).order_by(
                "in_date",  # type: date (not datetime)
                "id",  # rely on autoincrement to sort multiple states from same day
            )

            label_history: list[set[int]] = []
            label_dates: list[datetime.date] = []
            for sh in state_history:
                label_ids: list[int] = []
                label_ids.append(sh.state_id)
                if sh.iana_flag:
                    label_ids.append(self.label_name_to_id[IANA_FLAG_LABEL_NAME])
                if sh.ref_flag:
                    label_ids.append(self.label_name_to_id[REF_FLAG_LABEL_NAME])
                label_history.append(set(label_ids))
                label_dates.append(sh.in_date)

            assert len(label_history) == len(label_dates)
            if not label_history:
                continue  # TODO: refactor this so other unrelated code can follow in the loop.

            def _update_latest_history_date(
                history_date: datetime.date,
            ) -> HistoricalRfcToBe:
                h: HistoricalRfcToBe = rfc_to_be.history.latest()
                h.history_date = datetime.datetime(
                    history_date.year,
                    history_date.month,
                    history_date.day,
                    tzinfo=datetime.timezone.utc,
                )
                return h

            history: list[HistoricalRfcToBe] = []

            # override date of doc's first change (+)
            # TODO: this isn't right - the concept of history needs to be refactored
            # to be accounted at the actual RfcToBe creation and any other changes need
            # to be reflected in that history.
            history.append(_update_latest_history_date(min(label_dates)))

            # first set of labels (always additions since doc has no labels initially)
            for label_id in sorted(label_history[0]):
                rfc_to_be.labels.add(labels_cached[label_id])
                history.append(_update_latest_history_date(label_dates[0]))
            # subsequent sets of labels (subtractions and/or additions)
            for (old, _), (new, date) in pairwise(zip(label_history, label_dates)):
                # if no intersection, clear existing labels and then add all new labels at once
                intersection: set[int] = old & new
                if not intersection:
                    rfc_to_be.labels.set([])
                    history.append(_update_latest_history_date(date))
                    rfc_to_be.labels.set([labels_cached[id_] for id_ in sorted(new)])
                    history.append(_update_latest_history_date(date))
                    continue
                # otherwise, add and remove labels one by one
                removed: set[int] = old - new
                added: set[int] = new - old
                for label_id in sorted(removed):
                    rfc_to_be.labels.remove(labels_cached[label_id])
                    history.append(_update_latest_history_date(date))
                for label_id in sorted(added):
                    rfc_to_be.labels.add(labels_cached[label_id])
                    history.append(_update_latest_history_date(date))

            # bulk update on HistoricalRfcToBe.history_date
            HistoricalRfcToBe.objects.bulk_update(history, ["history_date"])

    def get_assignments(self):
        rpcperson_by_initials = dict()
        for row in Editors.objects.all():
            if row.name in self.people_pks:
                rpcperson_by_initials[row.initials] = RpcPerson.objects.get(
                    datatracker_person__datatracker_id=self.people_pks[row.name]
                )
        # Focusing first on in-process docs - it's unclear what to do about past assignments
        for doc in tqdm(
            RfcToBe.objects.filter(disposition_id="in_progress"),
            desc="get_assignments",
            disable=None,
        ):
            if doc not in self.index_id_of:
                print(f"Skipping assignments for {doc}")
                continue
            index_id = self.index_id_of[doc]
            assignments = EditorAssignments.objects.filter(doc_key=index_id).order_by(
                "-role_key"
            )
            active_assignment = None
            for assignment in assignments:
                if assignment.initials == "XX":
                    continue
                if assignment.initials in rpcperson_by_initials:
                    if not active_assignment:
                        active_assignment = assignment
                    Assignment.objects.get_or_create(
                        rfc_to_be=doc,
                        person=rpcperson_by_initials[assignment.initials],
                        role_id={
                            1: "first_editor",
                            2: "enqueuer",
                            3: "second_editor",
                            4: "publisher",
                        }[assignment.role_key],
                        state=(
                            "assigned" if assignment == active_assignment else "done"
                        ),  # TODO: should this use "in progress"?
                    )

    def dt_stdlevelname_slug(self, index_name: str) -> str:
        """Returns the datatracker StdLevelName slug matching strings from the Index table
        ['bcp', 'ds', 'exp', 'hist', 'inf', 'std', 'ps', 'unkn']
        """
        name_map = {
            "best current practice": "bcp",
            "internet standard": "std",
            "standard": "std",
            "std": "std",
            "draft standard": "ds",
            "proposed standard": "ps",
            "informational": "inf",
            "historic": "hist",
            "experimental": "exp",
        }
        return name_map.get(index_name.strip().lower(), "unkn")

    def stream_slug_from_index(self, index):
        ssp_id = WorkingGroup.objects.get(wg_name=index.source).ssp_id
        mapping = {
            1: "ietf",
            3: "iab",
            4: "irtf",
            6: "ise",
            8: "editorial",
        }
        if not ssp_id in mapping:
            raise Exception("Unexpected stream (ssp_id = {ssp_id}) encountered")
        return mapping[ssp_id]

    def source_format_id_from_index(self, index):
        # Note that this field in the old database conflates what format was submitted,
        # where in the process of conversion a document is, and what format was converted
        # into, and it _loses_ information along the way - we can only migrate what it knows
        # at the time of migration, history before that does not exist in that database.
        src = index.xml_file
        mapping = {
            0: "txt",  # Verify with Jean that this is the right interpretation
            1: "xml-v2",
            2: "nroff",
            5: "xml-v3",
        }
        if src in mapping:
            return mapping[src]
        else:
            return "unknown"

    def import_clusters(self):
        garbage_in = {
            "draft-draft-ietf-teas-rfc8776-update": "draft-ietf-teas-rfc8776-update",
            "draft-ietf-ldapbis-syntaxes+": "draft-ietf-ldapbis-syntaxes",
        }
        draft_names = set(Clusters.objects.values_list("draft_base", flat=True))
        for bad_name in garbage_in.keys():
            draft_names.discard(bad_name)
        for good_name in garbage_in.values():
            draft_names.add(good_name)
        self.update_draftinfo(list(draft_names))
        ClusterInfo = namedtuple("ClusterInfo", ["document", "order_token"])
        cluster_docs = defaultdict(set)
        for offset, cluster_member in enumerate(
            tqdm(
                Clusters.objects.exclude(cluster_id=""),
                desc="build cluster order",
                disable=None,
            ),
            start=50000,
        ):
            index_row_qs = Index.objects.filter(
                type="RFC",
                state_id__in=IN_PROGRESS_STATES + PUBLISHED_STATES,
            ).filter(draft__regex=r"^" + cluster_member.draft_base + r"-\d\d$")
            if index_row_qs.count() > 1:
                print(f"Unexpected Index matches for {cluster_member.draft_base}")
                for i in index_row_qs:
                    print(f"{i.pk} {i.draft} {i.state_id}, {i.doc_id}")
                exit(-1)
            index_row = index_row_qs.first()
            if index_row is not None and index_row.doc_id not in [None, "", "RFC"]:
                order_token = int(index_row.doc_id[3:])  # the RFC number
            else:
                # Document is not yet in queue (index_row is None) or
                # no RFC number is assigned yet - we are arbitrarily ordering these initially
                # to come after the ones that have RFC numbers in the order they appeared
                # in the Clusters table. The RPC will manually reorder after import if
                # necessary.
                order_token = offset
            name = cluster_member.draft_base
            if name in garbage_in:
                name = garbage_in[name]
            cluster_docs[int(cluster_member.cluster_id[1:])].add(
                ClusterInfo(
                    document=Document.objects.get(name=name),
                    order_token=order_token,
                )
            )
        for cluster_id in tqdm(
            sorted(cluster_docs.keys()), desc="import clusters", disable=None
        ):
            cluster, _ = Cluster.objects.get_or_create(number=cluster_id)
            ordered_members = sorted(
                cluster_docs[cluster_id], key=lambda o: int(o.order_token)
            )
            for order, member in enumerate(ordered_members, start=1):
                ClusterMember.objects.create(
                    cluster=cluster, doc=member.document, order=order
                )

    def parse_authors(self, authors):
        """Heuristically split the rfced.models.Index.authors field

        Returns a list of dicts, one for each name, containing:
            titlepage_name : name that the current search and metadata
                             pages show for each author (maybe normalized
                             for commas)
            lastname : last name without any suffix
            initials : full set of initials
            is_editor : whether the name was adorned with ", Ed."
        """
        results = []
        author = dict()
        elements = [e.strip() for e in authors.split(",")]
        elements.reverse()
        while len(elements) > 0:
            token = elements.pop(0)
            if token == "Ed.":
                author["is_editor"] = True
                token = elements.pop(0)
            else:
                author["is_editor"] = False
            if token in ["II", "III", "et al."]:
                author["comma_separated_suffix"] = token
                token = elements.pop(0)
            else:
                author["comma_separated_suffix"] = ""
            last_period_space_index = token.rfind(". ")
            if last_period_space_index == -1:
                author["lastname"] = token
                author["initials"] = ""
            else:
                author["lastname"] = token[last_period_space_index + 2 :]
                author["initials"] = token[: last_period_space_index + 1]
            titlepage_name = ""
            if len(author["initials"]) > 0:
                titlepage_name = f"{author["initials"]} "
            titlepage_name = titlepage_name + author["lastname"]
            if len(author["comma_separated_suffix"]) > 0:
                titlepage_name = (
                    titlepage_name + f", {author["comma_separated_suffix"]}"
                )
            if author["is_editor"]:
                titlepage_name = titlepage_name + f", Ed."
            author["titlepage_name"] = titlepage_name
            results.append(author)
            author = dict()
        return results

    def get_authors_and_approvers(self):
        transform_helper = TransformHelper()
        rfc_match_helper = RfcMatchHelper()
        draft_match_helper = DraftMatchHelper()
        email_lists = (
            Index.objects.filter(pk__in=self.index_id_of.values())
            .exclude(email__isnull=True)
            .values_list("email", flat=True)
        )
        emails = [e.strip().lower() for line in email_lists for e in line.split(",")]
        email_to_person = persons_by_email(emails)

        dt_rfc_authors = dict(
            [
                (i.rfc_number, i.authors)
                for i in rfc_authors(
                    list(
                        RfcToBe.objects.filter(disposition_id="published").values_list(
                            "rfc_number", flat=True
                        )
                    )
                )
            ]
        )

        dt_draft_authors = dict(
            [
                (i.draft_name, i.authors)
                for i in draft_authors(
                    list(
                        RfcToBe.objects.filter(
                            disposition_id="in_progress"
                        ).values_list("draft__name", flat=True)
                    )
                )
            ]
        )

        fails = 0
        for rfc in tqdm(
            RfcToBe.objects.filter(disposition_id__in=["published", "in_progress"]),
            desc="building authors and approvers",
            disable=None,
        ):
            index = Index.objects.get(pk=self.index_id_of[rfc])
            rfced_authors = self.parse_authors(index.authors)
            index_email = index.email
            if index_email is None:
                index_email = ""
            rfced_emails = set([a.strip().lower() for a in index_email.split(",")])
            # Get the authors the datatracker knows about
            # create an RfcAuthor object for each of those, consuming the matching
            #   name out of index.authors and emails from index.email
            dt_authors = (
                dt_rfc_authors[rfc.rfc_number]
                if rfc.disposition_id == "published"
                else dt_draft_authors[rfc.draft.name]
            )
            for dt_author in dt_authors:
                match = False
                for rfced_author in rfced_authors:
                    if (
                        rfc.disposition_id == "published"
                        and rfc_match_helper.manually_confirmed_match(
                            rfc.rfc_number,
                            rfced_author["titlepage_name"],
                            dt_author.person_pk,
                        )
                    ):
                        match = True
                        break
                    elif (
                        rfc.disposition_id == "in_progress"
                        and draft_match_helper.manually_confirmed_match(
                            rfc.draft.name,
                            rfced_author["titlepage_name"],
                            dt_author.person_pk,
                        )
                    ):
                        match = True
                        break
                    elif (
                        dt_author.last_name.lower()
                        == transform_helper.transformed_lastname(
                            rfced_author["lastname"]
                        ).lower()
                        and (
                            dt_author.initials == ""
                            or dt_author.initials[0].lower()
                            == transform_helper.transformed_first_initial(
                                rfced_author
                            ).lower()
                        )
                    ):
                        match = True
                        break
                if not match:
                    print("-----------------------")
                    print(
                        f"RFC {rfc.rfc_number} / {rfc.draft.name if rfc.draft else 'no draft'}"
                    )
                    print(f"dt_author: {dt_author}")
                    for rfced_author in rfced_authors:
                        print(f"rfced_author: {rfced_author}")
                    fails += 1
                else:
                    rfced_authors.remove(rfced_author)
                    rfced_emails = rfced_emails - set(dt_author.email_addresses)
                    datatracker_person, _ = DatatrackerPerson.objects.get_or_create(
                        datatracker_id=dt_author.person_pk
                    )
                    RfcAuthor.objects.create(
                        rfc_to_be=rfc,
                        titlepage_name=rfced_author["titlepage_name"],
                        is_editor=rfced_author["is_editor"],
                        datatracker_person=datatracker_person,
                        # TODO look for a matching auth_48 approval
                    )

            # create an RfcAuthor object with no DatatrackerPerson for any remaining
            #   names out of index.authors
            for rfced_author in rfced_authors:
                if len(rfced_author["titlepage_name"]) > 128:
                    print(
                        f"***** TITLEPAGENAME TOO LONG {rfced_author['titlepage_name']}"
                    )
                RfcAuthor.objects.create(
                    rfc_to_be=rfc,
                    titlepage_name=rfced_author["titlepage_name"][
                        :128
                    ],  # TODO remove truncation
                    is_editor=rfced_author["is_editor"],
                    datatracker_person=None,
                    # TODO look for a matching auth_48 approval
                )

            # create an AdditionalEmail object for each email remaining in index.email
            for address in rfced_emails:
                AdditionalEmail.objects.create(email=address, rfc_to_be=rfc)

        print(f"Total failures: {fails}")

        # TODO - deal with the non-author approvers

        # TODO: deal with in_progress
        # TODO: deal with not in [published, in_progress]
