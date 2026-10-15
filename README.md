# Review Automation

A GitHub Actions workflow runs once a day. It pulls every guest review from Hospitable and logs new ones on the **All Reviews Log** board in monday.com:

| Hospitable property country | monday workspace | Reviews board | Linked to |
|---|---|---|---|
| US | USA Automated (Trial) | All Reviews Log (`18435520124`) | Properties Master (USA) |
| CA | Canada Automated(Trial) | All Reviews Log (`18435476757`) | Properties Master (Canada) |

Each new review becomes one item:

- **Name:** the reviewer's name.
- **Review Date**, **Platform** and **Property**.
- **Property Link:** links to the property's row in Properties Master, matched on that board's *Hospitable ID* column.
- **Overall Rating:** the total stars.
- **Cleanliness, Communication, Check-in, Accuracy, Location, Value:** the individual category stars.
- **Review Text:** the public review, followed by the guest's private feedback and any comments they left on individual categories.
- **Flagged for Action:** checked when the overall rating **or any category rating** is below 5 stars.
- **Host Responded:** checked once a response is posted. This is also updated later on reviews that are already logged.
- **Review ID:** the Hospitable review UUID. It's used to skip reviews that are already on the board, so re-running the workflow never creates duplicates.

## Setup

Add these as repository secrets (**Settings → Secrets and variables → Actions**):

| Secret | Where to get it |
|---|---|
| `HOSPITABLE_API_TOKEN` | Hospitable → Apps → API access → create a personal access token |
| `MONDAY_API_TOKEN` | monday.com → avatar → Developers → My access tokens |

## Running

- **Scheduled:** daily at 11:17 UTC, which is 7:17 AM Eastern during daylight time.
- **Manually:** Actions → *Sync Hospitable reviews to monday.com* → Run workflow. Tick **dry_run** to see what would be added without writing anything.
- **Locally:** `HOSPITABLE_API_TOKEN=... MONDAY_API_TOKEN=... DRY_RUN=true python sync_reviews.py`

## Notes

- **New properties:** add the property to the right Properties Master and fill in its *Hospitable ID*. Reviews logged before that are added without a Property Link.
- **Tests:** `python -m unittest discover -s tests`
