"""Google Finance USD/EUR and USD/KRW via installed Chrome/Edge browser.
Install: py -m pip install playwright
Run: py google_finance_browser_probe.py
"""
import json
import re
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

PAIRS = ('USD-EUR', 'USD-KRW')
OUT = Path.home() / 'google_finance_diagnostics'
OUT.mkdir(exist_ok=True)


def read_pair(page, pair):
    url = f'https://www.google.com/finance/quote/{pair}?hl=en'
    response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(1800)
    page.screenshot(path=str(OUT / f'{pair}.png'), full_page=False)
    (OUT / f'{pair}.html').write_text(page.content(), encoding='utf-8')
    if response is None or response.status >= 400:
        raise RuntimeError(f'HTTP {response.status if response else "no response"}')
    body = page.locator('body').inner_text(timeout=10000)
    expected = pair.replace('-', ' / ')
    if expected.lower() not in body.lower():
        raise ValueError(f'Expected pair {expected} absent; page may be blocked or challenged')
    # Prefer the displayed quote node, not unrelated prices in the sidebar.
    for selector in ('div.YMlKec.fxKbKc', 'div.YMlKec', '[data-last-price]'):
        loc = page.locator(selector)
        for idx in range(min(loc.count(), 5)):
            node = loc.nth(idx)
            raw = node.get_attribute('data-last-price') or node.inner_text()
            raw = raw.strip().replace(',', '')
            if re.fullmatch(r'\d+(?:\.\d+)?', raw):
                value = float(raw)
                if (pair == 'USD-EUR' and 0.3 < value < 2.0) or (pair == 'USD-KRW' and 500 < value < 3000):
                    return value, selector
    # DOM fallback: first numeric line shortly after the exact currency pair heading.
    lines = [x.strip() for x in body.splitlines() if x.strip()]
    for idx, line in enumerate(lines):
        if line.upper().replace(' ', '') == pair.replace('-', '/').upper():
            for next_line in lines[idx + 1:idx + 9]:
                if re.fullmatch(r'[\d,]+(?:\.\d+)?', next_line):
                    value = float(next_line.replace(',', ''))
                    if (pair == 'USD-EUR' and 0.3 < value < 2) or (pair == 'USD-KRW' and 500 < value < 3000):
                        return value, 'heading-neighbor'
    raise ValueError(f'Price not identified; inspect {OUT / (pair + ".png")}')


def main():
    errors = []
    values = {}
    with sync_playwright() as playwright:
        browser = None
        for channel in ('chrome', 'msedge'):
            try:
                browser = playwright.chromium.launch(channel=channel, headless=False)
                print(f'Using installed browser: {channel}')
                break
            except Exception as exc:
                errors.append(f'{channel}: {exc}')
        if browser is None:
            raise RuntimeError('Chrome/Edge launch failed: ' + ' | '.join(errors))
        try:
            context = browser.new_context(locale='en-US')
            page = context.new_page()
            for pair in PAIRS:
                try:
                    value, method = read_pair(page, pair)
                    values[pair] = value
                    print(f'{pair}: {value:.6f} [{method}]')
                except Exception as exc:
                    errors.append(f'{pair}: {exc}')
                    print(f'{pair}: FAILED - {exc}')
            if len(values) == 2:
                print(f'EUR/KRW (cross): {values["USD-KRW"] / values["USD-EUR"]:.2f}')
        finally:
            browser.close()
    result = {'checked_at_local': datetime.now().astimezone().isoformat(), 'rates': values, 'errors': errors}
    (OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Diagnostics:', OUT)


if __name__ == '__main__':
    main()
