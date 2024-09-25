# Copyright The IETF Trust 2023, All Rights Reserved
# -*- coding: utf-8 -*-

from collections import defaultdict, namedtuple
import rpcapi_client

from tqdm import tqdm

from django.core.management.base import BaseCommand

from datatracker.models import DatatrackerPerson, Document
from datatracker.rpcapi import with_rpcapi

from rfced.models import Clusters, EditorAssignments, Editors, Index, WorkingGroup

from ...models import (
    Assignment,
    Cluster,
    ClusterMember,
    RfcToBe,
    RpcPerson,
    SourceFormatName,
    StdLevelName,
    StreamName,
)

# Arcana
IN_PROGRESS_STATES = [1, 2, 4, 10, 12, 13, 15, 17, 18, 22, 23, 20]
PUBLISHED_STATES = [14]


@with_rpcapi
def get_rfc_original_streams(*, rpcapi: rpcapi_client.DefaultApi):
    original_stream_list = rpcapi.get_rfc_original_streams().original_stream
    original_streams = dict()
    for item in original_stream_list:
        original_streams[item.rfc_number] = item.stream
    return original_streams


@with_rpcapi
def update_documents(docnames, *, rpcapi: rpcapi_client.DefaultApi):
    documents = rpcapi.get_drafts_by_names(docnames)
    for name in tqdm(docnames, desc="update_documents"):
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

        # TODO: not doing anything with authors...


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

        SourceFormatName.objects.get_or_create(
            slug="nroff",
            name="nroff",
            desc="Source was submitted in nroff",
            used=False,  # THIS was why we needed to add used...
        )

    def handle(self, *args, **options):
        self.build_rpc_people()
        self.get_published_rfcs()
        self.get_in_process_docs()
        self.get_assignments()
        self.import_clusters()

        # TODO

        # Get withdrawn docs

        # Account for "not issued" index rows (did any have any work to capture?)

        # Handle other document types (?)
        #   >>> Counter(Index.objects.values_list('type',flat=True))
        #   Counter({'RFC': 9775, 'IEN': 208, 'BCP': 31, 'STD': 27})
        # Maybe make a Subseries model.

        # END TODO

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
        update_documents([name.strip()[:-3] for name in names])
        # Fixup what we can fixup
        update_documents(
            [
                "draft-ietf-madman-dsa-mib-1",
            ]
        )
        original_streams = get_rfc_original_streams()

        for row in tqdm(rfc_qs, desc="get_pubished_rfcs"):
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
            # TODO walk states and apply labels (with history)

    def get_in_process_docs(self):
        ip_qs = Index.objects.filter(type="RFC", state_id__in=IN_PROGRESS_STATES)
        names = (
            ip_qs.exclude(draft__isnull=True)
            .exclude(draft="")
            .exclude(pub_date__month=4, pub_date__day=1)
            .values_list("draft", flat=True)
        )
        update_documents([name.strip()[:-3] for name in names])

        for row in tqdm(ip_qs, desc="get_in_process_docs"):
            is_apr1 = (
                row.pub_date and row.pub_date.month == 4 and row.pub_date.day == 1
            ) or False
            found_doc = None
            if not is_apr1 and row.draft is not None and row.draft != "":
                found_doc = Document.objects.filter(name=row.draft.strip()[:-3]).first()
                if not found_doc:
                    print(f"Skipping {row.doc_id} - problem with {row.draft}")
                    continue
            rfc_to_be = RfcToBe.objects.create(
                disposition_id="in_progress",
                is_april_first_rfc=is_apr1,
                draft=found_doc,
                rfc_number=int(row.doc_id[3:]) if row.doc_id != "RFC" else None,
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
            # TODO walk states and apply labels (with history)

    def get_assignments(self):
        rpcperson_by_initials = dict()
        for row in Editors.objects.all():
            if row.name in self.people_pks:
                rpcperson_by_initials[row.initials] = RpcPerson.objects.get(
                    datatracker_person__datatracker_id=self.people_pks[row.name]
                )
        # Focusing first on in-process docs - it's unclear what to do about past assignments
        for doc in tqdm(
            RfcToBe.objects.filter(disposition_id="in_progress"), desc="get_assignments"
        ):
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
        update_documents(list(draft_names))
        ClusterInfo = namedtuple("ClusterInfo", ["document", "order_token"])
        cluster_docs = defaultdict(set)
        for offset, cluster_member in enumerate(
            tqdm(Clusters.objects.exclude(cluster_id=""), desc="build cluster order"),
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
        for cluster_id in tqdm(sorted(cluster_docs.keys()), desc="import clusters"):
            cluster, _ = Cluster.objects.get_or_create(number=cluster_id)
            ordered_members = sorted(
                cluster_docs[cluster_id], key=lambda o: int(o.order_token)
            )
            for order, member in enumerate(ordered_members, start=1):
                ClusterMember.objects.create(
                    cluster=cluster, doc=member.document, order=order
                )
