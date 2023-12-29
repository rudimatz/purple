# Copyright The IETF Trust 2023, All Rights Reserved
# -*- coding: utf-8 -*-

import rpcapi_client

from django.core.management.base import BaseCommand

from datatracker.models import DatatrackerPerson, Document
from datatracker.rpcapi import with_rpcapi

from rfced.models import EditorAssignments, Editors, Index

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

        self.todo_std_level, _ = StdLevelName.objects.get_or_create(
            slug="todo",
            name="Todo-stdlevel",
            desc="Don't understand std levels yet.",
        )

        self.todo_stream_name, _ = StreamName.objects.get_or_create(
            slug="todo",
            name="Todo-streamname",
            desc="Don't understand streams yet",
        )

        self.unknown_boilerplate, _ = TlpBoilerplateChoiceName.objects.get_or_create(
            slug="unknown",
            name="Boilerplate Unknown",
            desc="Don't know what boilerplate was used",
        )

        self.unknown_submitted_format, _ = SourceFormatName.objects.get_or_create(
            slug="unknown",
            name="Submitted Format Unknown",
            desc="Don't know what formats were submitted",
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
            RfcToBe.objects.get_or_create(
                disposition_id="published",
                is_april_first_rfc=is_apr1,
                draft=found_doc if not is_apr1 else None,
                rfc_number=int(row.doc_id[3:]),
                cluster=None,  # TODO: populate by walking Clusters table
                order_in_cluster=1,  # TODO: :point_up:
                submitted_format=self.unknown_submitted_format,  # TODO: verify that there's nothing currently captured
                submitted_std_level=self.todo_std_level,  # TODO
                submitted_boilerplate=self.unknown_boilerplate,  # TODO - populate those we _do_ know
                submitted_stream=self.todo_stream_name,  # TODO
                intended_std_level=self.todo_std_level,  # TODO
                intended_boilerplate=self.unknown_boilerplate,  # TODO
                intended_stream=self.todo_stream_name,  # TODO
                external_deadline=None,  # TODO - capture known ones?
                internal_goal=None,  # TODO - does the rfced db capture this?
            )
            # TODO walk states and apply labels (with history)

        print(
            "Skipped the following as they had no draft names populated (model breaks)"
        )
        print(sorted(nodraft))
        print("")
        print("Skipped the following known problematic drafts")
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
                submitted_format=self.unknown_submitted_format,  # TODO: verify that there's nothing currently captured
                submitted_std_level=self.todo_std_level,  # TODO
                submitted_boilerplate=self.unknown_boilerplate,  # TODO - populate those we _do_ know
                submitted_stream=self.todo_stream_name,  # TODO
                intended_std_level=self.todo_std_level,  # TODO
                intended_boilerplate=self.unknown_boilerplate,  # TODO
                intended_stream=self.todo_stream_name,  # TODO
                external_deadline=None,  # TODO - capture known ones?
                internal_goal=None,  # TODO - does the rfced db capture this?
            )
            # TODO walk states and apply labels (with history)

        print(
            "Skipped the following as they had no draft names populated (model breaks)"
        )
        print(sorted(nodraft))
        print("")
        print("Skipped the following known problematic drafts")
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
                        state="assigned"
                        if assignment == active_assignment
                        else "done",  # TODO: should this use "in progress"?
                    )
