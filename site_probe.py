"""Manual public-page connectivity checks; no credentials or email sending."""
import platform
import subprocess
import time

import primp
import requests

from monitor import HEADERS, fetch_page


def main():
    print(f"Platform: {platform.platform()}", flush=True)
    urls = [
        "https://www.gaiabike.pt/search?q=Orbea+Orca+M30",
        "https://www.gaiabike.pt/product/orbe-orca-m30-branco",
    ]
    failures = 0
    for url in urls:
        print(f"URL: {url}", flush=True)
        for method in ("requests", "primp", "curl-http1", "curl-http2"):
            started = time.monotonic()
            try:
                if method == "requests":
                    response = requests.get(url, headers=HEADERS, timeout=20)
                    print(method, response.status_code, len(response.content), flush=True)
                elif method == "primp":
                    client = primp.Client(impersonate="chrome_146", timeout=20)
                    response = client.get(url)
                    print(method, response.status_code, len(response.content), flush=True)
                else:
                    result = subprocess.run(
                        ["curl", "--http1.1" if method == "curl-http1" else "--http2",
                         "--max-time", "20", "--location", "--silent", "--show-error",
                         "--output", "NUL" if platform.system() == "Windows" else "/dev/null",
                         "--user-agent", HEADERS["User-Agent"],
                         "--write-out", "%{http_code} %{size_download}", url],
                        capture_output=True, text=True, timeout=25,
                    )
                    print(method, result.returncode, result.stdout, result.stderr[:300], flush=True)
            except Exception as exc:
                print(method, type(exc).__name__, str(exc)[:300], flush=True)
            print(f"Elapsed: {time.monotonic() - started:.1f}s", flush=True)
        page, soup = fetch_page(url)
        print("MONITOR", len(page), bool(soup), flush=True)
        if soup is None or not soup.title or "gaiabike" not in soup.title.get_text().lower():
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
