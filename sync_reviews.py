#!/usr/bin/env python3
"""Daily sync: Hospitable guest reviews -> monday.com "All Reviews Log" boards.

For every Hospitable property, pulls all reviews and adds any that are not yet
on the monday board for that property's country (USA / Canada). Each new item
is linked to the property's row in that workspace's Properties Master (matched
on the "Hospitable ID" column). Reviews with an overall rating, or any category
rating, below 5 stars get "Flagged for Action" checked.

Existing items are never rewritten; the only change made to them is ticking
"Host Responded" once Hospitable reports a host response.

Environment:
  HOSPITABLE_API_TOKEN  Hospitable personal access token (required)
  MONDAY_API_TOKEN      monday.com API token (required)
  DRY_RUN               "true" to log what would change without writing
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOSPITABLE_API = "https://public.api.hospitable.com/v2"
MONDAY_API = "https://api.monday.com/v2"

# country code -> monday boards for that workspace
BOARDS = {
    "US": {"label": "USA Automated (Trial)", "reviews": 18435520124, "properties": 18435507823},
    "CA": {"label": "Canada Automated(Trial)", "reviews": 18435476757, "properties": 18435476437},
}

# Hospitable detailed_ratings type -> monday column title
CATEGORY_COLUMNS = {
    "cleanliness": "Cleanliness",
    "communication": "Communication",
    "checkin": "Check-in",
    "accuracy": "Accuracy",
    "location": "Location",
    "value": "Value",
}

LONG_TEXT_LIMIT = 2000
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- HTTP

def http_json(url, *, headers, body=None, retries=4):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = int(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                log(f"  HTTP {e.code} from {url}, retrying in {wait}s")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode()[:500]}") from e
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise


def hospitable_get(path, params=None):
    url = f"{HOSPITABLE_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return http_json(url, headers={
        "Authorization": f"Bearer {os.environ['HOSPITABLE_API_TOKEN']}",
        "Accept": "application/json",
    })


def hospitable_paginate(path, params):
    page = 1
    while True:
        resp = hospitable_get(path, {**params, "page": page, "per_page": 100})
        yield from resp.get("data", [])
        meta = resp.get("meta") or {}
        if page >= meta.get("last_page", page):
            return
        page += 1


def monday(query, variables=None):
    resp = http_json(MONDAY_API, headers={
        "Authorization": os.environ["MONDAY_API_TOKEN"],
        "Content-Type": "application/json",
        "API-Version": "2024-10",
    }, body={"query": query, "variables": variables or {}})
    if resp.get("errors"):
        raise RuntimeError(f"monday API error: {json.dumps(resp['errors'])[:1000]}")
    return resp["data"]


# ---------------------------------------------------------------------- transform

def category_ratings(review):
    """{column title: rating} for the categories the guest actually rated (>0)."""
    out = {}
    for r in (review.get("private") or {}).get("detailed_ratings") or []:
        title = CATEGORY_COLUMNS.get(r.get("type"))
        if title and r.get("rating"):
            out[title] = r["rating"]
    return out


def overall_rating(review):
    return (review.get("public") or {}).get("rating")


def needs_flag(review):
    """True when the overall rating or any rated category is below 5 stars."""
    ratings = [overall_rating(review), *category_ratings(review).values()]
    return any(r is not None and r < 5 for r in ratings)


def reviewer_name(review):
    guest = review.get("guest") or {}
    name = " ".join(p for p in (guest.get("first_name"), guest.get("last_name")) if p).strip()
    return name or "Unknown guest"


def review_text(review):
    public = ((review.get("public") or {}).get("review") or "").strip()
    private = review.get("private") or {}
    feedback = (private.get("feedback") or "").strip()
    parts = [public] if public else []
    if feedback:
        parts.append(f"Private feedback: {feedback}")
    comments = [
        f"- {CATEGORY_COLUMNS.get(r['type'], r['type'].title())} ({r.get('rating')}★): {r['comment'].strip()}"
        for r in private.get("detailed_ratings") or []
        if (r.get("comment") or "").strip()
    ]
    if comments:
        parts.append("Category comments:\n" + "\n".join(comments))
    text = "\n\n".join(parts)
    if len(text) > LONG_TEXT_LIMIT:
        text = text[: LONG_TEXT_LIMIT - 1] + "…"
    return text


def build_column_values(review, property_name, property_item_id, cols):
    """monday column_values JSON for a new review item. `cols` maps title -> column id."""
    values = {
        cols["Property"]: property_name,
        cols["Platform"]: review.get("platform") or "",
        cols["Review ID"]: review["id"],
        cols["Review Text"]: {"text": review_text(review)},
    }
    if review.get("reviewed_at"):
        values[cols["Review Date"]] = {"date": review["reviewed_at"][:10]}
    if overall_rating(review) is not None:
        values[cols["Overall Rating"]] = str(overall_rating(review))
    for title, rating in category_ratings(review).items():
        values[cols[title]] = str(rating)
    if needs_flag(review):
        values[cols["Flagged for Action"]] = {"checked": "true"}
    if review.get("responded_at") or (review.get("public") or {}).get("response"):
        values[cols["Host Responded"]] = {"checked": "true"}
    if property_item_id and cols.get("Property Link"):
        values[cols["Property Link"]] = {"item_ids": [int(property_item_id)]}
    return values


# ------------------------------------------------------------------------- monday

REQUIRED_COLUMNS = [
    "Property", "Platform", "Review Date", "Overall Rating", *CATEGORY_COLUMNS.values(),
    "Review Text", "Flagged for Action", "Host Responded", "Review ID",
]


def board_columns(board_id):
    data = monday("query($id:[ID!]){boards(ids:$id){columns{id title type}}}", {"id": [board_id]})
    cols = {c["title"]: c["id"] for c in data["boards"][0]["columns"]}
    link = next((c["id"] for c in data["boards"][0]["columns"]
                 if c["type"] == "board_relation" and c["title"] == "Property Link"), None)
    missing = [t for t in REQUIRED_COLUMNS if t not in cols]
    if missing:
        raise RuntimeError(f"Board {board_id} is missing columns: {missing}")
    cols["Property Link"] = link
    return cols


def board_items(board_id, column_ids):
    query = """query($id:[ID!],$cols:[String!]){boards(ids:$id){items_page(limit:500){cursor
      items{id name column_values(ids:$cols){id text}}}}}"""
    next_query = """query($cursor:String!,$cols:[String!]){next_items_page(limit:500,cursor:$cursor){cursor
      items{id name column_values(ids:$cols){id text}}}}"""
    page = monday(query, {"id": [board_id], "cols": column_ids})["boards"][0]["items_page"]
    while True:
        yield from page["items"]
        if not page.get("cursor"):
            return
        page = monday(next_query, {"cursor": page["cursor"], "cols": column_ids})["next_items_page"]


def property_index(board_id):
    """Hospitable property UUID -> Properties Master item id."""
    data = monday("query($id:[ID!]){boards(ids:$id){columns{id title}}}", {"id": [board_id]})
    id_col = next((c["id"] for c in data["boards"][0]["columns"] if c["title"] == "Hospitable ID"), None)
    if not id_col:
        raise RuntimeError(f"Properties board {board_id} has no 'Hospitable ID' column")
    index = {}
    for item in board_items(board_id, [id_col]):
        uuid = (item["column_values"][0]["text"] or "").strip()
        if uuid and uuid != "—":
            index[uuid] = item["id"]
    return index


def create_item(board_id, name, values):
    if DRY_RUN:
        return "dry-run"
    data = monday(
        """mutation($board:ID!,$name:String!,$vals:JSON!){
          create_item(board_id:$board,item_name:$name,column_values:$vals,create_labels_if_missing:false){id}}""",
        {"board": board_id, "name": name[:255], "vals": json.dumps(values)},
    )
    return data["create_item"]["id"]


def mark_responded(board_id, item_id, col_id):
    if DRY_RUN:
        return
    monday(
        """mutation($board:ID!,$item:ID!,$vals:JSON!){
          change_multiple_column_values(board_id:$board,item_id:$item,column_values:$vals){id}}""",
        {"board": board_id, "item": item_id, "vals": json.dumps({col_id: {"checked": "true"}})},
    )


# --------------------------------------------------------------------------- main

def main():
    for var in ("HOSPITABLE_API_TOKEN", "MONDAY_API_TOKEN"):
        if not os.environ.get(var):
            sys.exit(f"Missing required environment variable {var}")
    if DRY_RUN:
        log("DRY RUN: nothing will be written to monday.com")

    # Load each workspace's board layout, property links and already-logged reviews.
    targets = {}
    for country, cfg in BOARDS.items():
        cols = board_columns(cfg["reviews"])
        existing = {}
        for item in board_items(cfg["reviews"], [cols["Review ID"], cols["Host Responded"]]):
            vals = {cv["id"]: cv["text"] for cv in item["column_values"]}
            review_id = (vals.get(cols["Review ID"]) or "").strip()
            if review_id:
                existing[review_id] = {"item_id": item["id"], "responded": vals.get(cols["Host Responded"]) == "v"}
        props = property_index(cfg["properties"])
        targets[country] = {**cfg, "cols": cols, "existing": existing, "props": props}
        log(f"{cfg['label']}: {len(existing)} reviews already logged, {len(props)} linked properties")

    stats = {"created": 0, "flagged": 0, "responded": 0, "skipped": 0}
    changes = []
    for prop in hospitable_paginate("/properties", {}):
        country = ((prop.get("address") or {}).get("country_code") or "").upper()
        target = targets.get(country)
        if not target:
            # Fall back to whichever Properties Master lists this property.
            target = next((t for t in targets.values() if prop["id"] in t["props"]), None)
        if not target:
            log(f"! Skipping {prop.get('name')} ({prop['id']}): country '{country}' has no board")
            changes.append(f"SKIPPED property {prop.get('name')}: country '{country}' has no board")
            stats["skipped"] += 1
            continue

        prop_name = prop.get("name") or prop.get("public_name") or prop["id"]
        prop_item = target["props"].get(prop["id"])
        if not prop_item:
            log(f"! {prop_name} ({prop['id']}) is not on {target['label']} Properties Master; "
                "reviews will be logged without a Property Link")
            changes.append(f"UNLINKED property {prop_name}: not on {target['label']} Properties Master")

        for review in hospitable_paginate(f"/properties/{prop['id']}/reviews", {"include": "guest"}):
            cols = target["cols"]
            known = target["existing"].get(review["id"])
            if known:
                if not known["responded"] and review.get("responded_at"):
                    mark_responded(target["reviews"], known["item_id"], cols["Host Responded"])
                    changes.append(f"RESPONDED {target['label']} | {prop_name} | {reviewer_name(review)}")
                    known["responded"] = True
                    stats["responded"] += 1
                continue

            values = build_column_values(review, prop_name, prop_item, cols)
            item_id = create_item(target["reviews"], reviewer_name(review), values)
            target["existing"][review["id"]] = {"item_id": item_id, "responded": bool(review.get("responded_at"))}
            stats["created"] += 1
            flagged = needs_flag(review)
            stats["flagged"] += flagged
            line = (f"{target['label']} | {prop_name} | {reviewer_name(review)} | "
                    f"{overall_rating(review)}★ | {review.get('reviewed_at', '')[:10]}"
                    + ("  [FLAGGED]" if flagged else ""))
            log(f"+ {line}")
            changes.append(f"NEW {line}")

    log(f"\nDone. New reviews: {stats['created']} ({stats['flagged']} flagged), "
        f"marked responded: {stats['responded']}, properties skipped: {stats['skipped']}")
    if os.environ.get("GITHUB_ACTIONS"):
        # Annotation shows on the run page and is readable through the checks API.
        body = (f"New: {stats['created']} ({stats['flagged']} flagged), responded: {stats['responded']}, "
                f"skipped properties: {stats['skipped']}\n" + "\n".join(changes[:400]))
        body = body.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        log(f"::notice title=Review sync{' (dry run)' if DRY_RUN else ''}::{body}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Review sync{' (dry run)' if DRY_RUN else ''}\n\n"
                    f"| New reviews | Flagged (<5★) | Marked responded | Properties skipped |\n"
                    f"|---|---|---|---|\n"
                    f"| {stats['created']} | {stats['flagged']} | {stats['responded']} | {stats['skipped']} |\n")


if __name__ == "__main__":
    main()
