"""Capture original complete Chrome window images of the local Neo4j Browser.

Optional setup: pip install -r requirements-browser.txt
Run after benchmark: python scripts/capture_neo4j.py
Only the browser window is captured; screenshots are not cropped or edited afterwards.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from ctypes import wintypes

from dotenv import load_dotenv
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
QUERIES = {
    'kg_count.png': 'MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC;',
    'kg_cross_kb.png': 'MATCH p=(:CaseReport)-[:ALLEGES]->(:Crime)<-[:DEFINES]-(:Article) RETURN p LIMIT 10;',
    'kg_my_case.png': "MATCH p=(:Person {name:'Trần Thanh Tuấn'})-[:HAS_PARTICIPATION]->(:Participation)-[:ACCUSED_OF]->(:Crime)<-[:DEFINES]-(:Article) MATCH q=(:Participation)-[:IN_REPORT]->(:CaseReport) WHERE nodes(q)[0]=nodes(p)[1] RETURN p,q;",
}


def capture_window(title: str, target: Path) -> None:
    user32 = ctypes.windll.user32
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        if title in text.value and user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    if not windows:
        raise RuntimeError(f'Browser window not found: {title}')
    hwnd = windows[-1]
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError('GetWindowRect failed')
    # PrintWindow captures this HWND even when another user window covers it.
    # No foreground keystrokes and no cropping/editing of the resulting image.
    width, height = rect.right - rect.left, rect.bottom - rect.top
    gdi = ctypes.windll.gdi32
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    dc = user32.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory, 2):
            raise RuntimeError('PrintWindow failed')
        class Header(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                        ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                        ('image_size', wintypes.DWORD), ('x', wintypes.LONG), ('y', wintypes.LONG),
                        ('used', wintypes.DWORD), ('important', wintypes.DWORD)]
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        data = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(memory, bitmap, 0, height, data, ctypes.byref(header), 0):
            raise RuntimeError('GetDIBits failed')
        Image.frombytes('RGB', (width, height), data.raw, 'raw', 'BGRX').save(target)
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user32.ReleaseDC(hwnd, dc)


def main() -> None:
    load_dotenv(ROOT / '.env')
    ctypes.windll.user32.SetProcessDPIAware()
    out = ROOT / 'report' / 'img'
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=False,
                                    args=['--window-position=0,0', '--start-maximized', '--disable-gpu',
                                          '--force-device-scale-factor=0.8'])
        page = browser.new_page(no_viewport=True)
        page.goto('http://localhost:7474/browser/', wait_until='networkidle')
        page.locator('input[name=username]').fill(os.getenv('NEO4J_USER', 'neo4j'))
        page.locator('input[name=password]').fill(os.getenv('NEO4J_PASSWORD', 'password123'))
        page.get_by_role('button', name='Connect', exact=True).click()
        page.get_by_text('Dismiss', exact=True).click(timeout=10000)
        page.get_by_title('Collapse Sidebar', exact=True).click()
        editor = page.locator('[contenteditable=true]').first
        for filename, query in QUERIES.items():
            editor.fill(':clear')
            editor.press('Control+Enter')
            page.wait_for_timeout(300)
            editor.fill(query)
            editor.press('Control+Enter')
            page.get_by_text('Started streaming', exact=False).first.wait_for(timeout=15000)
            if filename == 'kg_count.png':
                page.get_by_title('Save and open in tab', exact=True).click()
                page.wait_for_timeout(2000)
                dismiss = page.get_by_text('Dismiss', exact=True)
                if dismiss.count():
                    dismiss.click()
                collapse = page.get_by_title('Collapse Sidebar', exact=True)
                if collapse.count():
                    collapse.click()
            if filename != 'kg_count.png':
                page.get_by_text('Results overview', exact=True).wait_for()
                page.get_by_role('button', name='Zoom to fit', exact=True).click()
            page.wait_for_timeout(1200)
            # Browser retains the old frame's scroll position after :clear; reset native scroll.
            page.evaluate('''() => {
                for (const el of document.querySelectorAll('*')) {
                    if (el.scrollTop) el.scrollTop = 0;
                }
            }''')
            page.mouse.move(50, 160)
            page.wait_for_timeout(300)
            # Identify our controlled window uniquely, even if the user also has Browser open.
            page.evaluate("document.title = 'KG Bonus Evidence Capture'")
            page.wait_for_timeout(300)
            capture_window('KG Bonus Evidence Capture', out / filename)
            if filename == 'kg_count.png':
                page.get_by_title('Stream', exact=True).click()
            (out / filename.replace('.png', '.cypher')).write_text(query + '\n', encoding='utf-8')
            print(f'Saved report/img/{filename}', flush=True)
        browser.close()


if __name__ == '__main__':
    main()
