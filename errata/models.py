from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.utils import timezone
from rpc.models import RfcToBe


class Name(models.Model):
    slug = models.CharField(max_length=32, primary_key=True)
    name = models.CharField(max_length=255)
    desc = models.TextField(blank=True)
    used = models.BooleanField(default=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class AutoDateTimeField(models.DateTimeField):
    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if value is None:
            value = timezone.now()
        return value


class Errata(models.Model):
    """
    Model representing an erratum.
    """

    rfc_to_be = models.ForeignKey(
        RfcToBe, on_delete=models.PROTECT, related_name="errata"
    )
    status = models.ForeignKey(
        "Status",
        on_delete=models.PROTECT,
        default="reported",
        related_name="errata",
    )
    type = models.ForeignKey(
        "Type", on_delete=models.PROTECT, related_name="errata"
    )
    section = models.TextField(blank=True)
    orig_text = models.TextField(blank=True)
    corrected_text = models.TextField(blank=True)
    submitter_name = models.CharField(max_length=80, blank=True)
    submitter_email = models.EmailField(max_length=120, blank=True)
    submitter_dt_person = models.ForeignKey(
        "datatracker.DatatrackerPerson", on_delete=models.PROTECT,
        related_name="errata_submitter_dt_person"
    )
    notes = models.TextField(blank=True)
    submitted_at = models.DateField()
    posted_at = models.DateField(blank=True)
    verifier_dt_person = models.ForeignKey(
        "datatracker.DatatrackerPerson", null=True, on_delete=models.PROTECT,
        related_name="errata_verifier_dt_person"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = AutoDateTimeField()
    format = ArrayField(
        models.CharField(
            max_length=10, choices=[("HTML", "HTML"), ("PDF", "PDF"), ("TXT", "TXT")]
        ),
        default=list,
        blank=True,
        help_text="A list of formats. Possible values: 'HTML', 'PDF', and 'TXT'.",
    )

    def __str__(self):
        return f"Erratum {self.id} for RFC {self.rfc_to_be.id}"


class Status(Name):
    pass


class Type(Name):
    pass


class Log(models.Model):
    """
    Model representing the log of changes or updates to errata.
    """

    errata = models.ForeignKey(
        "Errata", on_delete=models.PROTECT, related_name="logs_errata"
    )
    verifier_dt_person = models.ForeignKey(
        "datatracker.DatatrackerPerson", null=True, on_delete=models.PROTECT,
        related_name="logs_verifier_dt_person"
    )
    status = models.ForeignKey(
        "Status", on_delete=models.PROTECT, related_name="logs_status"
    )
    type = models.ForeignKey(
        "Type", on_delete=models.PROTECT, related_name="logs_type"
    )
    editor_dt_person = models.ForeignKey(
        "datatracker.DatatrackerPerson", on_delete=models.PROTECT,
        related_name="logs_editor_dt_person"
    )
    section = models.TextField(blank=True)
    orig_text = models.TextField(blank=True)
    corrected_text = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Log {self.id} for Erratum {self.errata_id}"

