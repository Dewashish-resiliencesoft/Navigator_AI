"""Product Explore: crawl product → Google/public web → bio + knowledge.

1) Sign in + crawl the Client's product site (Playwright).
2) LLM plans searches; browser opens Google (DDG fallback), then visits
   top 3–4 public result pages for company/product context.
3) Writes explore markdown, fills empty Company bio fields, read-only topology.

Not a demo-flow builder. Does not write demo_playlist or explored flows.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

import yaml

_lock = threading.Lock()
_active: ProductExploreJob | None = None
# Hold long enough for ~1s dashboard poll to paint each finishing step.
_STEP_HOLD_S = 1.35
# Brief so at least one poll paints phase=done while still active; float owns ~2s celebrate.
_DONE_HOLD_S = 0.45


@dataclass
class ProductExploreJob:
    job_id: str
    product_id: str
    start_url: str
    phase: str = "starting"
    # starting | signing_in | crawling | web_research |
    # writing_bio | updating_knowledge | done | error
    pages_seen: int = 0
    max_pages: int = 25
    progress_pct: float = 0.0
    current_url: str = ""
    current_title: str = ""
    looking_at: str = ""
    error: str | None = None
    done: bool = False
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    updated_at: str = ""


def active_job() -> ProductExploreJob | None:
    with _lock:
        return _active


def _touch(job: ProductExploreJob, **kw: Any) -> None:
    for k, v in kw.items():
        setattr(job, k, v)
    job.updated_at = datetime.now(timezone.utc).isoformat()


def _set_progress(job: ProductExploreJob, pct: float) -> None:
    job.progress_pct = max(0.0, min(100.0, float(pct)))
    job.updated_at = datetime.now(timezone.utc).isoformat()


def status_dict(*, product_id: str | None = None) -> dict[str, Any]:
    job = active_job()
    pid = product_id or (job.product_id if job else None)
    base: dict[str, Any]
    if job is None:
        base = {"active": False}
    else:
        base = {
            "active": not job.done,
            "job_id": job.job_id,
            "product_id": job.product_id,
            "phase": job.phase if not job.done else ("done" if not job.error else "error"),
            "pages_seen": job.pages_seen,
            "max_pages": job.max_pages,
            "progress_pct": round(job.progress_pct, 1),
            "current_url": job.current_url,
            "current_title": job.current_title,
            "looking_at": job.looking_at,
            "error": job.error,
            "done": job.done,
            "start_url": job.start_url,
            "updated_at": job.updated_at,
        }
    if pid:
        base["artifacts"] = artifact_checklist(
            pid, job if job and job.product_id == pid else None
        )
    return base


def artifact_checklist(
    product_id: str, job: ProductExploreJob | None = None
) -> list[dict[str, Any]]:
    """What Product Explore updates — pending / running / ok / warn for Client UI."""
    from navigator.knowledge.company_bio import load_bio
    from navigator.knowledge.knowledge_merge import load_knowledge_bundle
    from navigator.knowledge.topology import load_topology

    bundle = load_knowledge_bundle(product_id)
    explore_md = (bundle.get("explore_markdown") or "").strip()
    merged_at = bundle.get("merged_at")
    topo = load_topology(product_id)
    page_count = int(topo.get("page_count") or 0)
    bio = load_bio(product_id)
    bio_fields = [f for f in (bio.get("fields") or []) if isinstance(f, dict)]
    bio_filled = sum(1 for f in bio_fields if str(f.get("value") or "").strip())

    active = job is not None and not job.done
    phase = (job.phase if job else "") or ""
    err = (job.error if job else None) or None
    pages_seen = int(job.pages_seen if job else 0)

    def _disk(ok: bool, *, warn_if_err: bool = True) -> str:
        if active:
            return "pending"
        if err and warn_if_err and not ok:
            return "warn"
        return "ok" if ok else "pending"

    finishing = ("writing_bio", "updating_knowledge", "writing")

    crawl_status = "pending"
    if active and phase in ("starting", "signing_in", "crawling"):
        crawl_status = "running"
    elif active and phase in ("web_research", *finishing, "done"):
        crawl_status = "ok" if pages_seen > 0 else "warn"
    elif err and not page_count and not explore_md:
        crawl_status = "warn"
    elif page_count > 0 or pages_seen > 0 or explore_md:
        crawl_status = "ok"

    web_status = "pending"
    if active and phase == "web_research":
        web_status = "running"
    elif active and phase in (*finishing, "done"):
        web_status = "ok"
    elif explore_md and "## Public web notes" in explore_md:
        web_status = "ok"
    elif not active and explore_md and not err:
        web_status = "ok"
    elif err and not explore_md:
        web_status = "warn"

    bio_running = active and phase == "writing_bio"
    knowledge_running = active and phase == "updating_knowledge"
    write_running = active and phase in finishing
    explore_status = (
        "running"
        if write_running
        else ("warn" if err and not explore_md else _disk(bool(explore_md)))
    )
    bio_status = (
        "running"
        if bio_running or (active and phase == "writing")
        else ("warn" if err and bio_filled == 0 else _disk(bio_filled > 0))
    )
    topo_status = (
        "running"
        if knowledge_running or (active and phase == "writing")
        else ("warn" if err and page_count == 0 else _disk(page_count > 0))
    )
    merge_status = (
        "running"
        if knowledge_running or (active and phase == "writing")
        else ("warn" if err and not merged_at else _disk(bool(merged_at)))
    )

    crawl_detail = (
        f"{pages_seen} pages this run"
        if active and pages_seen
        else (f"{page_count} pages mapped" if page_count else "Signs in, then walks your product")
    )
    if err and crawl_status == "warn":
        crawl_detail = err

    return [
        {
            "id": "crawl",
            "label": "Crawl product (after login)",
            "detail": crawl_detail,
            "status": crawl_status,
        },
        {
            "id": "web_research",
            "label": "Public web enrichment",
            "detail": "Uses names found on-site to gather public product context",
            "status": web_status,
        },
        {
            "id": "explore_md",
            "label": "Explore notes",
            "detail": "Writes explore markdown for the agent",
            "status": explore_status,
        },
        {
            "id": "bio",
            "label": "Company bio gaps",
            "detail": (
                f"{bio_filled} field{'s' if bio_filled != 1 else ''} filled"
                if bio_filled
                else "Fills empty or weak bio fields"
            ),
            "status": bio_status,
        },
        {
            "id": "topology",
            "label": "Automated product map",
            "detail": "Read-only site topology (not the live demo graph)",
            "status": topo_status,
        },
        {
            "id": "knowledge_merge",
            "label": "Canonical knowledge merge",
            "detail": (
                f"Last merge {merged_at}"
                if merged_at
                else "Merges your notes + explore into canonical .md"
            ),
            "status": merge_status,
        },
    ]


def stop_job() -> dict[str, Any]:
    with _lock:
        job = _active
        if job is None:
            raise RuntimeError("no active product explore")
        job.stop.set()
        pid = job.product_id
    if job.thread:
        job.thread.join(timeout=60)
    return status_dict(product_id=pid)


def ack_job(*, product_id: str) -> dict[str, Any]:
    """Clear finished job so refresh does not re-show success UI."""
    global _active
    with _lock:
        job = _active
        if job is None:
            return status_dict(product_id=product_id)
        if job.product_id != product_id:
            return status_dict(product_id=product_id)
        if not job.done:
            raise RuntimeError("product explore still running — stop first")
        _active = None
    return status_dict(product_id=product_id)


def start_job(
    *,
    product_id: str,
    start_url: str,
    login_config_fn: Callable[[], Any] | None = None,
    browser_ws: str = "",
    max_pages: int = 25,
) -> ProductExploreJob:
    global _active
    url = (start_url or "").strip()
    if not url:
        raise RuntimeError("start_url required")
    if "://" not in url:
        url = f"https://{url}"
    with _lock:
        if _active is not None and not _active.done:
            raise RuntimeError("a product explore is already running")
        job = ProductExploreJob(
            job_id=str(uuid.uuid4()),
            product_id=product_id,
            start_url=url,
            max_pages=max_pages,
            looking_at="Starting…",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        _active = job

    def _run() -> None:
        try:
            _crawl_and_write(
                job,
                login_config_fn=login_config_fn,
                browser_ws=browser_ws,
                max_pages=max_pages,
            )
        except Exception as exc:  # noqa: BLE001
            raw = str(exc)
            if "Executable doesn't exist" in raw or "playwright install" in raw.lower():
                job.error = (
                    "Chromium missing for Playwright. "
                    "Run on the API host: .venv/bin/python -m playwright install chromium"
                )
            else:
                job.error = raw
            job.phase = "error"
            job.looking_at = job.error
            print(f"[product-explore] crash:\n{traceback.format_exc()}", flush=True)
        finally:
            job.done = True
            if job.phase not in ("done", "error"):
                job.phase = "done" if not job.error else "error"
            if not job.error:
                _set_progress(job, 100)
                if not (job.looking_at or "").strip():
                    job.looking_at = "Company bio and knowledge updated"
            job.updated_at = datetime.now(timezone.utc).isoformat()

    t = threading.Thread(target=_run, name="product-explore", daemon=True)
    job.thread = t
    t.start()
    return job


def _slug(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (label or "").strip().lower()).strip("_")
    return (s[:48] or "page")


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc.lower()) == (pb.scheme, pb.netloc.lower())


def _crawl_and_write(
    job: ProductExploreJob,
    *,
    login_config_fn: Callable[[], Any] | None,
    browser_ws: str,
    max_pages: int,
) -> None:
    from playwright.sync_api import sync_playwright

    from navigator.automation.explore.perceive import inventory
    from navigator.automation.playwright_env import ensure_playwright_browsers
    from navigator.knowledge.knowledge_merge import (
        auto_merge_knowledge,
        save_explore_markdown,
    )
    from navigator.knowledge.topology import save_topology

    ensure_playwright_browsers()

    job.max_pages = max_pages
    pages: dict[str, dict[str, Any]] = {}
    texts: list[str] = []
    queue: list[str] = [job.start_url]
    seen: set[str] = set()
    web_notes: list[str] = []
    company = ""
    host = ""
    corpus = ""

    with sync_playwright() as pw:
        ws = (browser_ws or "").strip()
        if ws:
            browser = pw.chromium.connect(ws, timeout=20_000)
        else:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
                    raise RuntimeError(
                        "Chromium not installed for Playwright. "
                        "On the API host run: .venv/bin/python -m playwright install chromium"
                    ) from exc
                raise
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        try:
            if login_config_fn is not None:
                try:
                    cfg = login_config_fn()
                    login_url = str(getattr(cfg, "login_url", "") or "")
                    user = str(getattr(cfg, "username", "") or "")
                    password = str(getattr(cfg, "password", "") or "")
                    if login_url and user and password:
                        from navigator.automation.browser.product_login import (
                            login_product,
                        )

                        _touch(
                            job,
                            phase="signing_in",
                            current_url=login_url,
                            current_title="Sign-in",
                            looking_at="Signing in with Product Login…",
                        )
                        _set_progress(job, 4)
                        login_product(
                            page,
                            url=login_url,
                            email=user,
                            password=password,
                        )
                        # Prefer crawling from post-login landing.
                        try:
                            post = page.url
                            if post and post not in queue:
                                queue.insert(0, post)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as exc:  # noqa: BLE001
                    print(f"[product-explore] login skipped: {exc}", flush=True)

            _touch(
                job,
                phase="crawling",
                looking_at="Walking product pages…",
            )
            _set_progress(job, 8)

            while queue and len(seen) < max_pages and not job.stop.is_set():
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                _touch(
                    job,
                    current_url=url,
                    current_title="",
                    looking_at=f"Opening {urlparse(url).path or '/'}",
                )
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:  # noqa: BLE001
                    print(f"[product-explore] goto failed {url}: {exc}", flush=True)
                    continue
                final = page.url
                title = ""
                try:
                    title = page.title() or ""
                except Exception:  # noqa: BLE001
                    pass
                body_text = ""
                try:
                    body_text = page.evaluate(
                        "() => (document.body && document.body.innerText || '').slice(0, 8000)"
                    )
                except Exception:  # noqa: BLE001
                    body_text = ""
                els: list[dict[str, Any]] = []
                try:
                    els = inventory(page) or []
                except Exception:  # noqa: BLE001
                    els = []
                page_id = _slug(title) if title else _slug(urlparse(final).path or "home")
                base_id = page_id
                n = 2
                while page_id in pages:
                    page_id = f"{base_id}_{n}"
                    n += 1
                selectors: dict[str, str] = {}
                for i, el in enumerate(els[:40]):
                    label = (
                        el.get("text")
                        or el.get("label")
                        or el.get("aria_label")
                        or el.get("name")
                        or f"el_{i}"
                    )
                    alias = _slug(str(label))
                    if not alias or alias in selectors:
                        alias = f"{alias}_{i}" if alias else f"el_{i}"
                    css = ""
                    if el.get("testid"):
                        css = f"[data-testid=\"{el['testid']}\"]"
                    elif el.get("id"):
                        css = f"#{el['id']}"
                    elif el.get("name"):
                        css = f"[name=\"{el['name']}\"]"
                    if css:
                        selectors[alias] = css
                pages[page_id] = {
                    "url": final,
                    "title": title,
                    "selectors": selectors,
                }
                if body_text.strip():
                    texts.append(
                        f"## {title or page_id}\nURL: {final}\n\n{body_text.strip()[:3000]}"
                    )
                job.pages_seen = len(pages)
                # Product crawl owns ~0–70% of the bar.
                crawl_pct = 8 + (62 * len(pages) / max(1, max_pages))
                _touch(
                    job,
                    current_url=final,
                    current_title=title,
                    looking_at=f"Reading “{title or page_id}”",
                )
                _set_progress(job, crawl_pct)

                try:
                    hrefs = page.evaluate(
                        """() => Array.from(document.querySelectorAll('a[href]'))
                          .map(a => a.href).filter(Boolean).slice(0, 40)"""
                    )
                except Exception:  # noqa: BLE001
                    hrefs = []
                for href in hrefs or []:
                    abs_u = urljoin(final, str(href))
                    if not _same_origin(job.start_url, abs_u):
                        continue
                    parsed = urlparse(abs_u)
                    if parsed.fragment and not parsed.path:
                        continue
                    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if parsed.query:
                        clean += f"?{parsed.query}"
                    if clean not in seen and clean not in queue:
                        queue.append(clean)

            # Public web pass while Playwright is still open (real Google/DDG).
            if pages and not job.stop.is_set():
                first_title = next(iter(pages.values()), {}).get("title") or ""
                company = first_title.split("-")[0].strip() if first_title else ""
                host = urlparse(job.start_url).netloc.replace("www.", "")
                if not company:
                    company = host.split(".")[0].title() if host else "product"
                corpus = "\n\n".join(texts[:12])[:12_000]
                _touch(
                    job,
                    phase="web_research",
                    looking_at=f"Planning Google research for “{company}”…",
                    current_title="Google research",
                    current_url="https://www.google.com/",
                )
                _set_progress(job, 72)
                web_notes = _brain_web_research(
                    company,
                    host,
                    corpus,
                    job,
                    page=page,
                    start_url=job.start_url,
                )
                _set_progress(job, 85)
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass

    if job.stop.is_set() and not pages:
        job.phase = "done"
        job.error = "stopped before any pages"
        job.looking_at = "Stopped before any pages"
        return

    if not pages:
        job.phase = "done"
        job.error = job.error or "no pages crawled"
        job.looking_at = "No product pages found"
        return

    if not company:
        first_title = next(iter(pages.values()), {}).get("title") or ""
        company = first_title.split("-")[0].strip() if first_title else ""
        host = urlparse(job.start_url).netloc.replace("www.", "")
        if not company:
            company = host.split(".")[0].title() if host else "product"
        corpus = "\n\n".join(texts[:12])[:12_000]

    _touch(
        job,
        phase="writing_bio",
        looking_at="Filling company bio…",
        current_title="Company bio",
        current_url=job.start_url or "https://www.google.com/",
    )
    _set_progress(job, 90)
    time.sleep(_STEP_HOLD_S)
    if job.stop.is_set():
        job.looking_at = "Stopped"
        return

    explore_md = _build_explore_md(job.start_url, texts, pages, web_notes)
    save_explore_markdown(job.product_id, explore_md)
    _fill_bio_from_explore(
        job.product_id,
        start_url=job.start_url,
        company=company,
        corpus=corpus,
        web_notes=web_notes,
        page_ids=list(pages.keys()),
        job=job,
    )

    _touch(
        job,
        phase="updating_knowledge",
        looking_at="Updating knowledge…",
        current_title="Knowledge",
        current_url=job.start_url or "https://www.google.com/",
    )
    _set_progress(job, 95)
    time.sleep(_STEP_HOLD_S)
    if job.stop.is_set():
        job.looking_at = "Stopped"
        return

    topo = {
        "site": job.product_id,
        "base_url": f"{urlparse(job.start_url).scheme}://{urlparse(job.start_url).netloc}/",
        "version": 1,
        "pages": {
            pid: {
                "url": meta["url"],
                "title": meta.get("title") or pid,
                "selectors": meta.get("selectors") or {},
                "flows": {},
            }
            for pid, meta in pages.items()
        },
        "demo_playlist": [],
        "_meta": {"source": "product_explore", "non_demo": True},
    }
    save_topology(
        job.product_id,
        yaml.safe_dump(topo, sort_keys=False),
        page_count=len(pages),
    )
    auto_merge_knowledge(job.product_id)
    try:
        from navigator.core.settings import settings
        from navigator.knowledge.publish_index import index_knowledge_draft

        index_knowledge_draft(
            product_id=job.product_id,
            text=explore_md,
            revision=0,
            chroma_path=settings.chroma_path,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[product-explore] index skipped: {exc}", flush=True)

    _touch(
        job,
        phase="done",
        looking_at="Company bio and knowledge updated",
        current_title="Done",
        current_url=job.start_url or "",
    )
    _set_progress(job, 100)
    # Keep active so float can show success, then bounce-out after poll.
    time.sleep(_DONE_HOLD_S)


def _brain_complete(system: str, user: str) -> str:
    """Prefer Groq (same as knowledge merge), else configured reflect provider."""
    try:
        from navigator.core.settings import settings

        key = (settings.groq_api_key or "").strip()
        if key:
            from groq import Groq

            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model=settings.brain_phrasing_model or "llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=2500,
            )
            return (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[product-explore] groq brain skipped: {exc}", flush=True)
    try:
        from navigator.agent.providers import get_provider

        return get_provider().complete(system, user).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[product-explore] provider brain skipped: {exc}", flush=True)
        return ""


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}


def _unwrap_search_url(href: str) -> str:
    """Unwrap Google `/url?q=` and DDG `uddg=` redirects to the real target."""
    raw = (href or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        qs = parse_qs(parsed.query)
        if "google." in host and parsed.path.startswith("/url"):
            q = (qs.get("q") or qs.get("url") or [""])[0]
            if q.startswith("http"):
                return unquote(q)
        if "uddg" in qs:
            u = unquote(qs["uddg"][0])
            if u.startswith("http"):
                return u
    except Exception:  # noqa: BLE001
        pass
    return raw


def _is_noise_result_host(host: str, *, product_host: str = "") -> bool:
    h = (host or "").lower().replace("www.", "")
    if not h:
        return True
    if product_host and h == product_host.lower().replace("www.", ""):
        return True
    noise = (
        "google.",
        "googleapis.com",
        "gstatic.com",
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "duckduckgo.com",
    )
    return any(x in h for x in noise)


def _page_visible_text(page: Any, *, limit: int = 8000) -> str:
    try:
        return (
            page.evaluate(
                "() => (document.body && document.body.innerText || '').slice(0, "
                + str(int(limit))
                + ")"
            )
            or ""
        )
    except Exception:  # noqa: BLE001
        return ""


def _dismiss_google_consent(page: Any) -> None:
    for sel in (
        "#L2AGLb",
        "button#L2AGLb",
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
        "button:has-text('Accept All')",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=800):
                loc.click(timeout=2000)
                page.wait_for_timeout(400)
                return
        except Exception:  # noqa: BLE001
            continue


def _extract_google_result_hrefs(page: Any, *, limit: int = 8) -> list[str]:
    try:
        hrefs = page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              const push = (h) => {
                if (!h || seen.has(h)) return;
                seen.add(h);
                out.push(h);
              };
              for (const a of document.querySelectorAll('a[href]')) {
                const h = a.href || '';
                if (!h.startsWith('http')) continue;
                // Organic results: title links under #search / .g
                const inResult = a.closest('#search') || a.closest('.g') || a.closest('[data-sokoban-container]');
                if (!inResult) continue;
                push(h);
                if (out.length >= 20) break;
              }
              return out;
            }"""
        )
    except Exception:  # noqa: BLE001
        hrefs = []
    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs or []:
        u = _unwrap_search_url(str(h))
        if not u.startswith("http") or u in seen:
            continue
        host = urlparse(u).netloc.lower()
        if _is_noise_result_host(host):
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def _extract_ddg_result_hrefs(page: Any, *, limit: int = 8) -> list[str]:
    try:
        hrefs = page.evaluate(
            """() => Array.from(document.querySelectorAll('a.result__a[href], a[data-testid="result-title-a"][href]'))
              .map(a => a.href).filter(Boolean).slice(0, 20)"""
        )
    except Exception:  # noqa: BLE001
        hrefs = []
    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs or []:
        u = _unwrap_search_url(str(h))
        if not u.startswith("http") or u in seen:
            continue
        if _is_noise_result_host(urlparse(u).netloc):
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def _playwright_search_hits(
    page: Any,
    query: str,
    job: ProductExploreJob,
    *,
    limit: int = 6,
) -> list[str]:
    """Google in-browser first; DuckDuckGo HTML if Google yields <2 hits."""
    google_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&num=10"
    _touch(
        job,
        current_url=google_url,
        current_title="Google",
        looking_at=f"Google: {query}",
    )
    try:
        page.goto(google_url, wait_until="domcontentloaded", timeout=45_000)
        _dismiss_google_consent(page)
        try:
            page.wait_for_selector("#search a[href], .g a[href]", timeout=8_000)
        except Exception:  # noqa: BLE001
            pass
        hits = _extract_google_result_hrefs(page, limit=limit)
        if len(hits) >= 2:
            return hits
        print(
            f"[product-explore] google returned {len(hits)} hit(s) for {query!r} — trying DDG",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[product-explore] google search failed: {exc}", flush=True)
        hits = []

    ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    _touch(
        job,
        current_url=ddg_url,
        current_title="DuckDuckGo",
        looking_at=f"DuckDuckGo: {query}",
    )
    try:
        page.goto(ddg_url, wait_until="domcontentloaded", timeout=45_000)
        hits = _extract_ddg_result_hrefs(page, limit=limit)
        if hits:
            return hits
    except Exception as exc:  # noqa: BLE001
        print(f"[product-explore] ddg browser search failed: {exc}", flush=True)

    # Last resort: stdlib DDG HTML (often empty for bots).
    _touch(
        job,
        current_url="https://html.duckduckgo.com/",
        current_title="DuckDuckGo",
        looking_at=f"Fallback search: {query}",
    )
    return [
        h["url"]
        for h in _ddg_search(query, limit=limit)
        if (h.get("url") or "").startswith("http")
    ]


def _brain_web_research(
    company: str,
    host: str,
    corpus: str,
    job: ProductExploreJob,
    *,
    page: Any,
    start_url: str,
) -> list[str]:
    """LLM plans queries; Playwright Google + top 3–4 external page crawls."""
    plan_raw = _brain_complete(
        "You plan public-web research for a B2B product demo agent. "
        "Return ONLY JSON: "
        '{"queries":["..."],"urls":["https://..."],"company_site":"https://..."} '
        "queries: 2-4 Google-style searches for the company/product/parent company. "
        "urls: official marketing, about, pricing, LinkedIn company page if known. "
        "No markdown.",
        f"Company hint: {company}\nProduct host: {host}\n"
        f"On-site excerpts:\n{corpus[:5000] or '(none)'}",
    )
    plan = _parse_json_object(plan_raw)
    queries = [str(q).strip() for q in (plan.get("queries") or []) if str(q).strip()]
    if not queries:
        queries = [
            f"{company} {host} product",
            f"{company} company about",
            f"{host} pricing features",
        ]
    seed_urls = [str(u).strip() for u in (plan.get("urls") or []) if str(u).strip()]
    company_site = str(plan.get("company_site") or "").strip()
    if company_site and company_site not in seed_urls:
        seed_urls.insert(0, company_site)

    notes: list[str] = []
    seen_urls: set[str] = set()
    max_external = 4
    product_host = urlparse(start_url).netloc.lower().replace("www.", "")

    def _visit(url: str, *, label: str) -> None:
        nonlocal notes
        if job.stop.is_set() or len(seen_urls) >= max_external:
            return
        if not url.startswith("http") or url in seen_urls:
            return
        if _same_origin(start_url, url):
            return
        host_h = urlparse(url).netloc.lower()
        if _is_noise_result_host(host_h, product_host=product_host):
            # Allow linkedin company pages through noise filter.
            if "linkedin.com" not in host_h:
                return
        seen_urls.add(url)
        host_label = urlparse(url).netloc.replace("www.", "")
        _touch(
            job,
            current_url=url,
            current_title=host_label,
            looking_at=label or f"Reading {host_label}",
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:  # noqa: BLE001
            print(f"[product-explore] web goto failed {url}: {exc}", flush=True)
            # Soft fallback: stdlib fetch if Playwright blocked.
            body = _fetch_text(url, limit=2200)
            if body:
                notes.append(f"### {host_label}\nURL: {url}\n\n{body[:1800]}")
            return
        title = ""
        try:
            title = page.title() or ""
        except Exception:  # noqa: BLE001
            pass
        final = page.url or url
        _touch(
            job,
            current_url=final,
            current_title=title or host_label,
            looking_at=f"Reading “{title or host_label}”",
        )
        body = _page_visible_text(page, limit=4000)
        if not body.strip():
            body = _fetch_text(final, limit=2200)
        if body.strip():
            notes.append(
                f"### {title or host_label}\nURL: {final}\n\n{body.strip()[:1800]}"
            )

    # Prefer plan seed URLs first (still count toward max_external).
    for url in seed_urls:
        if len(seen_urls) >= max_external or job.stop.is_set():
            break
        _visit(url, label=f"Reading related site {urlparse(url).netloc}")

    for qi, query in enumerate(queries[:3]):
        if len(seen_urls) >= max_external or job.stop.is_set():
            break
        hits = _playwright_search_hits(page, query, job, limit=6)
        for href in hits:
            if len(seen_urls) >= max_external or job.stop.is_set():
                break
            _visit(
                href,
                label=f"Reading search result ({qi + 1}/{len(queries[:3])})",
            )
            _set_progress(
                job, 72 + (13 * len(seen_urls) / max(1, max_external))
            )

    if not notes:
        print(
            f"[product-explore] no external page notes after search "
            f"(company={company!r} host={host!r}) — DDG fallback",
            flush=True,
        )
        return _public_web_notes(company, host, job)
    return notes


def _bio_value_is_weak(
    key: str, value: str, *, company: str = "", enrichable: frozenset[str] | None = None
) -> bool:
    """True when explore may overwrite — empty, short, or company-name-only."""
    keys = enrichable or frozenset(
        {
            "about",
            "products",
            "industry",
            "competitors",
            "tagline",
            "target_market",
            "key_features",
            "usp",
            "pricing_model",
        }
    )
    v = (value or "").strip()
    if not v:
        return True
    if key not in keys:
        return False
    if len(v) < 40:
        return True
    c = (company or "").strip().lower()
    if c and v.lower() == c:
        return True
    return False


def _fill_bio_from_explore(
    product_id: str,
    *,
    start_url: str,
    company: str,
    corpus: str,
    web_notes: list[str],
    page_ids: list[str],
    job: ProductExploreJob,
) -> None:
    from navigator.knowledge.company_bio import DEFAULT_BIO_FIELDS, load_bio, save_bio

    enrichable = frozenset(
        {
            "about",
            "products",
            "industry",
            "competitors",
            "tagline",
            "target_market",
            "key_features",
            "usp",
            "pricing_model",
        }
    )

    def _weak(key: str, value: str) -> bool:
        return _bio_value_is_weak(key, value, company=company, enrichable=enrichable)

    bio = load_bio(product_id)
    fields = list(bio.get("fields") or [])
    by_key = {str(f.get("key")): f for f in fields if isinstance(f, dict)}
    for d in DEFAULT_BIO_FIELDS:
        if d["key"] not in by_key:
            row = dict(d)
            fields.append(row)
            by_key[d["key"]] = row

    need_keys = [
        k for k, row in by_key.items() if _weak(k, str(row.get("value") or ""))
    ]
    # Heuristics fill empty-only basics.
    heuristics = {
        "website": start_url,
        "company_name": company,
        "about": (corpus[:500] if corpus else ""),
        "products": ", ".join(page_ids[:8]),
    }
    changed = False
    for key, val in heuristics.items():
        if not val:
            continue
        row = by_key.get(key)
        if row is None:
            continue
        cur = str(row.get("value") or "").strip()
        if not cur or (key in enrichable and _weak(key, cur)):
            # Never clobber long user about with raw corpus snippet if already decent.
            if key == "about" and cur and len(cur) >= 40 and cur.lower() != (company or "").lower():
                continue
            row["value"] = val
            changed = True

    if need_keys and not job.stop.is_set():
        _touch(job, looking_at="Brain extracting company bio fields…")
        schema = [
            {"key": k, "label": str(by_key[k].get("label") or k)}
            for k in need_keys
            if k in by_key
        ]
        web_block = "\n".join(web_notes)[:5000] if web_notes else "(none)"
        if not web_notes:
            print(
                "[product-explore] web_notes empty — filling bio from on-site corpus only",
                flush=True,
            )
        raw = _brain_complete(
            "Extract company/product facts for a demo agent bio form. "
            "Return ONLY JSON object mapping field keys to short string values. "
            "Use empty string when unknown. Do not invent funding rounds or fake emails. "
            "Prefer official facts from the corpus and public web notes. "
            "Improve weak/short fields; keep factual.",
            f"Fields needed: {json.dumps(schema)}\n\n"
            f"Product URL: {start_url}\nCompany hint: {company}\n\n"
            f"## On-site crawl\n{corpus[:7000] or '(none)'}\n\n"
            f"## Public web notes\n{web_block}",
        )
        filled = _parse_json_object(raw)
        for key, val in filled.items():
            if key not in by_key:
                continue
            text = str(val or "").strip()
            if not text:
                continue
            cur = str(by_key[key].get("value") or "").strip()
            if not _weak(key, cur):
                continue
            # Never wipe long user-authored text (≥80 chars, not just company name).
            if (
                len(cur) >= 80
                and cur.lower() != (company or "").lower()
                and key in enrichable
            ):
                continue
            by_key[key]["value"] = text[:800]
            changed = True

    if changed:
        save_bio(product_id, {"fields": fields})
        print(
            f"[product-explore] bio updated "
            f"({sum(1 for f in fields if str(f.get('value') or '').strip())} fields filled)",
            flush=True,
        )


def _public_web_notes(
    company: str, host: str, job: ProductExploreJob
) -> list[str]:
    """Fallback public enrichment from DuckDuckGo HTML (fail-soft)."""
    query = f"{company} {host} product".strip()
    _touch(
        job,
        looking_at=f"Web search: {query}",
        current_url="https://html.duckduckgo.com/",
        current_title="DuckDuckGo",
    )
    hits = _ddg_search(query, limit=4)
    notes: list[str] = []
    for i, hit in enumerate(hits):
        if job.stop.is_set():
            break
        title = hit.get("title") or "Result"
        href = hit.get("url") or ""
        snippet = hit.get("snippet") or ""
        _touch(
            job,
            current_url=href,
            current_title=title,
            looking_at=f"Reading public page {i + 1}/{len(hits)}",
        )
        _set_progress(job, 72 + (12 * (i + 1) / max(1, len(hits))))
        body = ""
        if href.startswith("http"):
            body = _fetch_text(href, limit=2500)
        block = f"### {title}\n"
        if href:
            block += f"URL: {href}\n"
        if snippet:
            block += f"{snippet}\n"
        if body:
            block += f"\n{body[:1800]}\n"
        notes.append(block.strip())
    return notes


class _DDGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[dict[str, str]] = []
        self._in_result = False
        self._in_title = False
        self._in_snippet = False
        self._href = ""
        self._title = ""
        self._snippet = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        cls = ad.get("class", "")
        if tag == "div" and "result" in cls.split() and "result--" not in cls:
            self._in_result = True
            self._href = ""
            self._title = ""
            self._snippet = ""
        if self._in_result and tag == "a" and "result__a" in cls:
            self._in_title = True
            href = ad.get("href", "")
            if "uddg=" in href:
                qs = parse_qs(urlparse(href).query)
                self._href = unquote(qs.get("uddg", [href])[0])
            else:
                self._href = href
        if self._in_result and tag == "a" and "result__snippet" in cls:
            self._in_snippet = True
        if self._in_result and tag == "div" and "result__snippet" in cls:
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
        if tag in ("a", "div") and self._in_snippet:
            self._in_snippet = False
        if tag == "div" and self._in_result and self._title:
            self.hits.append(
                {
                    "title": self._title.strip(),
                    "url": self._href.strip(),
                    "snippet": self._snippet.strip(),
                }
            )
            self._in_result = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title += data
        if self._in_snippet:
            self._snippet += data


def _ddg_search(query: str, *, limit: int = 4) -> list[dict[str, str]]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    raw = _fetch_bytes(url)
    if not raw:
        return []
    parser = _DDGParser()
    try:
        parser.feed(raw.decode("utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for hit in parser.hits:
        u = hit.get("url") or ""
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def _fetch_bytes(url: str, *, timeout: float = 12.0) -> bytes:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "NavigatorAI-ProductExplore/1.0 (+knowledge)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read(400_000)
    except Exception as exc:  # noqa: BLE001
        print(f"[product-explore] fetch failed {url}: {exc}", flush=True)
        return b""


def _fetch_text(url: str, *, limit: int = 2500) -> str:
    raw = _fetch_bytes(url)
    if not raw:
        return ""
    html = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _build_explore_md(
    start_url: str,
    texts: list[str],
    pages: dict[str, dict[str, Any]],
    web_notes: list[str] | None = None,
) -> str:
    lines = [
        "# Product explore notes",
        "",
        f"Start URL: {start_url}",
        f"Pages discovered: {len(pages)}",
        "",
        "## Page index",
        "",
    ]
    for pid, meta in pages.items():
        lines.append(f"- **{pid}**: {meta.get('title') or ''} — `{meta.get('url')}`")
    lines.append("")
    lines.append("## Page content excerpts")
    lines.append("")
    lines.extend(texts[:20] if texts else ["(no visible text captured)"])
    if web_notes:
        lines.append("")
        lines.append("## Public web notes")
        lines.append("")
        lines.extend(web_notes)
    return "\n".join(lines).strip() + "\n"
