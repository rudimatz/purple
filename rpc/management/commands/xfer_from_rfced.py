# Copyright The IETF Trust 2023, All Rights Reserved
# -*- coding: utf-8 -*-

import rpcapi_client

from django.core.management.base import BaseCommand

from datatracker.models import DatatrackerPerson, Document
from datatracker.rpcapi import with_rpcapi

from rfced.models import EditorAssignments, Editors, Index, WorkingGroup

from ...models import (
    Assignment,
    RfcToBe,
    RpcPerson,
    SourceFormatName,
    StdLevelName,
    StreamName,
    TlpBoilerplateChoiceName,
)


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
    for name in docnames:
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
        self.people_pks = {
            # Direct name matches in datatracker
            "Aaron Falk": 21226,
            "Sarah Tarrant": 131267,
            "Jean Mahoney": 114455,
            "Aaron Stone": 110093,
            "Sandy Ginoza": 104401,
            "Alice Russo": 113811,
            "Marshika Szabo": 127045,
            "Reuben Esparza": 127195,
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

        SourceFormatName.objects.get_or_create(
            slug="nroff",
            name="nroff",
            desc="Source was submitted in nroff",
            used=False, # THIS was why we needed to add used...
        )

    def handle(self, *args, **options):
        self.build_rpc_people()
        self.get_published_rfcs()
        self.get_in_process_docs()
        self.get_assignments()

        # TODO
        # Get withdrawn docs
        # Handle other document types (?)
        #   >>> Counter(Index.objects.values_list('type',flat=True))
        #   Counter({'RFC': 9775, 'IEN': 208, 'BCP': 31, 'STD': 27})

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
        rfc_qs = Index.objects.filter(type="RFC", state_id=14).exclude(
            status="NOT ISSUED"
        )
        names = (
            rfc_qs.exclude(draft__isnull=True)
            .exclude(draft="")
            .exclude(pub_date__month=4, pub_date__day=1)
            .exclude(
                doc_id__in=["RFC2605", "RFC3018", "RFC6019", "RFC6342", "RFC7159"]
            )  # anomalous draft values
            .values_list("draft", flat=True)
        )
        # All remaining draft names include version numbers - strip them
        update_documents([name.strip()[:-3] for name in names])
        original_streams = get_rfc_original_streams()

        # First get published RFCs
        problematic = []
        nodraft = []
        for row in rfc_qs:
            is_apr1 = (
                row.pub_date and row.pub_date.month == 4 and row.pub_date.day == 1
            ) or False
            found_doc = None
            if not is_apr1:
                if row.draft is None or row.draft == "":
                    nodraft.append(row.doc_id)

                    continue  # TODO solve the problem
                # These are anomolies in the incoming data
                # Some are missing drafts, some are republications of RFCs because of errors
                # ('RFC3018', 'draft-bogdanov-umsp')
                # ('RFC2605', 'draft-ietf-madman-dsa-mib-1')
                # ('RFC6019', 'rfc4049bis')
                # ('RFC6342', 'draft-ietf-v6ops-v6-in-mobile-networks-rfc6312bis')
                # ('RFC7159', 'draft-ietf-json-rfc4627bis-rfc7159bis')
                if row.doc_id in [
                    "RFC2605",
                    "RFC3018",
                    "RFC6019",
                    "RFC6342",
                    "RFC7159",
                ]:
                    problematic.append(row.doc_id)
                    continue  # TODO solve the problem
                found_doc = Document.objects.filter(name=row.draft.strip()[:-3]).first()
                if not found_doc:
                    print(f"Skipping {row.doc_id} - problem with {row.draft}")
                    continue
            rfc_number = int(row.doc_id[3:])
            RfcToBe.objects.get_or_create(
                disposition_id="published",
                is_april_first_rfc=is_apr1,
                draft=found_doc if not is_apr1 else None,
                rfc_number=rfc_number,
                cluster=None,  # TODO: populate by walking Clusters table
                order_in_cluster=1,  # TODO: :point_up:
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
            # TODO walk states and apply labels (with history)

        print(
            f"Skipped the following {len(nodraft)} items as they had no draft names populated (model breaks)"
        )
        print(sorted(nodraft))
        print("")
        print("Skipped the following known problematic items")
        print(sorted(problematic))

    def get_in_process_docs(self):
        ip_qs = Index.objects.filter(
            state_id__in=[1, 2, 4, 10, 12, 13, 15, 17, 18, 22, 23, 20]
        )
        names = (
            ip_qs.exclude(draft__isnull=True)
            .exclude(draft="")
            .exclude(pub_date__month=4, pub_date__day=1)
            .values_list("draft", flat=True)
        )
        update_documents([name.strip()[:-3] for name in names])
        # First get published RFCs
        problematic = []
        nodraft = []
        for row in ip_qs:
            is_apr1 = (
                row.pub_date and row.pub_date.month == 4 and row.pub_date.day == 1
            ) or False
            found_doc = None
            if not is_apr1:
                if row.draft is None or row.draft == "":
                    nodraft.append(row.doc_id)

                    continue  # TODO solve the problem
                # These are no anomolies in this dataset

                if row.doc_id in []:
                    problematic.append(row.doc_id)
                    continue  # TODO solve the problem
                found_doc = Document.objects.filter(name=row.draft.strip()[:-3]).first()
                if not found_doc:
                    print(f"Skipping {row.doc_id} - problem with {row.draft}")
                    continue
            RfcToBe.objects.get_or_create(
                disposition_id="in_progress",
                is_april_first_rfc=is_apr1,
                draft=found_doc if not is_apr1 else None,
                rfc_number=int(row.doc_id[3:]) if row.doc_id != "RFC" else None,
                cluster=None,  # TODO: populate by walking Clusters table
                order_in_cluster=1,  # TODO: :point_up:
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
            # TODO walk states and apply labels (with history)

        print(
            f"Skipped the following {len(nodraft)} items as they had no draft names populated (model breaks)"
        )
        print(sorted(nodraft))
        print("")
        print("Skipped the following known problematic items")
        print(sorted(problematic))

    def get_assignments(self):
        rpcperson_by_initials = dict()
        for row in Editors.objects.all():
            if row.name in self.people_pks:
                rpcperson_by_initials[row.initials] = RpcPerson.objects.get(
                    datatracker_person__datatracker_id=self.people_pks[row.name]
                )
        # Focusing first on in-process docs - it's unclear what to do about past assignments
        for doc in RfcToBe.objects.filter(disposition_id="in_progress"):
            assignments = EditorAssignments.objects.filter(doc_key=doc.pk).order_by(
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
                            2: "second_editor",
                            3: "final_review_editor",
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
            0: "txt", # Verify with Jean that this is the right interpretation
            1: "xml-v2",
            2: "nroff",
            5: "xml-v3",
        }
        if src in mapping:
            return mapping[src]
        else:
            return "unknown"
