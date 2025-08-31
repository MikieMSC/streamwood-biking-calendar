import os
import re
import json
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse
from dateutil import parser as dp
import pytz
from ics import Calendar, Event
from bs4 import BeautifulSoup

# Playwright sync API
from playwright.sync_api import sync_playwright

PAGE_SLUG = "StreamwoodBiking"
BASE = "https://m.facebook.com/"
EVENTS_URL = f"{BASE}{PAGE_SLUG}/events/"

# Timezone to use if none is found explicitly
DEFAULT_TZ = "America/Chicago"

def unique(seq):
    seen = set()
    for x in seq:
        if x not in seen:
            seen.add(x)
            yield x

def extract_event_ids(html):
    """
    Parse any /events/<id> links from the listing page.
    """
    ids = []
    for m in re.finditer(r"/events/(\d+)", html):
        ids.append(m.group(1))
    return list(unique(ids))

def text_or_none(node):
    return node.get_text(strip=True) if node else None

def try_og_meta(soup, prop):
    tag = soup.find("meta", attrs={"property": prop})
    return tag["content"] if tag and tag.has_attr("content") else None

def parse_event_page(html):
    soup = BeautifulSoup(html, "lxml")

    # Prefer Open Graph if present
    title = try_og_meta(soup, "og:title")
    desc  = try_og_meta(soup, "og:description")
    url   = try_og_meta(soup, "og:url")

    # Heuristics for time/location
    # Try JSON fragments embedded in the HTML (Facebook often embeds data)
    start_dt = None
    end_dt = None
    location = None

    for script in soup.find_all("script"):
        txt = script.string or script.text or ""
        # print(f"txt: {txt}")
        if "event" in txt.lower() and ("start" in txt.lower() or "end" in txt.lower()):
            # Cheap attempt to extract ISO timestamps
            # print(f"txt: {txt}")
            for m in re.finditer(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:?\d{2}|Z)?)", txt):
                print(f"m: {m}")
                iso = m.group(1)
                try:
                    dt = dp.parse(iso)
                    if not start_dt:
                        start_dt = dt
                    elif not end_dt and dt >= start_dt:
                        end_dt = dt
                except Exception:
                    pass

    # Fallbacks: scan for date text
    if not start_dt:
        # Look for common date containers
        possible = soup.find_all(["time", "abbr"])
        for p in possible:
            try:
                start_dt = dp.parse(p.get("datetime") or p.get_text(" ", strip=True), fuzzy=True)
                break
            except Exception:
                continue

    # Location heuristics
    loc_labels = ["Location", "Place", "Venue", "Where"]
    for lbl in loc_labels:
        el = soup.find(string=re.compile(rf"\b{lbl}\b", re.I))
        if el:
            node = el.parent.find_next()
            if node:
                location = text_or_none(node)
                if location:
                    break

    return {
        "title": title or "Untitled Event",
        "description": desc or "",
        "url": url,
        "start": start_dt,
        "end": end_dt,
        "location": location,
    }

def build_ics(events, outfile="public/streamwood_biking.ics"):
    cal = Calendar()
    tz = pytz.timezone(DEFAULT_TZ)
    for e in events:
        ev = Event()
        ev.name = e["title"]
        if e["start"]:
            # ensure timezone-aware
            dt = e["start"]
            if dt.tzinfo is None:
                dt = tz.localize(dt)
            ev.begin = dt
        if e.get("end"):
            dt2 = e["end"]
            if dt2.tzinfo is None:
                dt2 = tz.localize(dt2)
            ev.end = dt2
        if e.get("url"):
            ev.url = e["url"]
        if e.get("description"):
            ev.description = e["description"]
        if e.get("location"):
            ev.location = e["location"]
        # Use event URL or ID as UID
        ev.uid = (e.get("url") or f"{PAGE_SLUG}-{int(time.time())}") + "@streamwood-biking"
        cal.events.add(ev)

    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    with open(outfile, "w", encoding="utf-8") as f:
        f.writelines(cal)
    print(f"wrote {outfile}")

def create_index(outfile="public/index.html"):
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    with open(outfile, "w", encoding="utf-8") as infil:
        infil.write("<html><body>This is test index for SB Cal<br><a href=\"event_id_urls.txt\">event_id_urls txt</a><br></body></html>")
    print(f"wrote {outfile}")

def main():
    cookie_header = os.environ.get("FB_COOKIE", "").strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        # Optionally inject Facebook cookies (if provided as a single header string).
        # Example value to store in secret: "c_user=...; xs=...; datr=...; sb=...;"
        if cookie_header:
            print("in cookie_header")
            cookies = []
            for kv in [c.strip() for c in cookie_header.split(";") if "=" in c]:
                name, value = kv.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".facebook.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                })
            context.add_cookies(cookies)

        page = context.new_page()
        page.goto(EVENTS_URL, wait_until="domcontentloaded", timeout=60000)
        # Some pages lazy-load; small scroll to reveal more
        for _ in range(3):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1200)

        html = page.content()
        if not html:
            print(f"no html")
        event_ids = extract_event_ids(html)
        print(f"event_ids: {event_ids}")
        event_ids = list(unique(event_ids))[:20]  # limit to most recent 20
        print(f"event_ids again: {event_ids}")
        if event_ids:
            print(f"event_ids in check: {event_ids}")
            os.makedirs(os.path.dirname("public/event_ids.txt"), exist_ok=True)
            with open("public/event_ids.txt", "w", encoding="utf-8") as efile:
#            with open("event_ids.txt", "w", encoding="utf-8") as efile:
                # efile.writelines(event_ids)
                efile.writelines(map(lambda x: x + '\n', event_ids))
                # efile.write("test line")
        
        results = []
        os.makedirs(os.path.dirname("public/event_id_urls.txt"), exist_ok=True)
        if os.path.exists("public/event_id_urls.txt"):
            os.remove("public/event_id_urls.txt")
#        if os.path.exists("event_id_urls.txt"):
#            os.remove("event_id_urls.txt")
        for eid in event_ids:
            url = urljoin(BASE, f"events/{eid}")
            with open("public/event_id_urls.txt", "a", encoding="utf-8") as eufile:
#            with open("event_id_urls.txt", "a", encoding="utf-8") as eufile:
                eufile.write(url + "\n")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            details = parse_event_page(page.content())
            print(f"details: {details}")
            if not details.get("url"):
                details["url"] = url
                print(f"details: {details}")
                print(f"")
            results.append(details)

        browser.close()

    # Filter obviously invalid (no start time found)
    results = [r for r in results if r.get("start")]
    build_ics(results)
    create_index()

if __name__ == "__main__":
    main()


