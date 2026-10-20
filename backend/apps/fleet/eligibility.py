"""Driver document eligibility, shared by on-demand dispatch and charter.

"Document status" is part of every dispatch filter: an expired PSV licence or
insurance removes a driver from the pool immediately (blueprint 8). The rule
lives here so on-demand dispatch and charter/hire cannot drift apart.
"""

from apps.fleet.models import Document


def valid_driver_ids(today):
    """Driver ids whose documents are all VERIFIED and unexpired.

    A driver with no documents at all is excluded: an unproved driver is not a
    valid one. A driver with a mix is excluded too, by the set difference, so a
    single expired document removes them from the pool.
    """
    valid, invalid = set(), set()
    for doc in Document.objects.filter(entity_type=Document.EntityType.DRIVER):
        expired = doc.expires_on is not None and doc.expires_on < today
        if doc.status == Document.Status.VERIFIED and not expired:
            valid.add(doc.entity_id)
        else:
            invalid.add(doc.entity_id)
    return valid - invalid
