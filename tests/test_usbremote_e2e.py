"""Connected receiver + phone interoperability: venv/bin/python tests/test_usbremote_e2e.py.

Controls the running receiver at localhost:5000. Does not change USB mappings.
"""
try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None
    expect = None

BASE = 'http://127.0.0.1:5000'


def run():
    if sync_playwright is None:
        print('playwright not installed; skipping USB remote E2E tests.')
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                    args=['--no-sandbox', '--disable-gpu'])
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        request = context.request

        def get(path):
            response = request.get(BASE + path)
            assert response.ok, response.text()
            return response.json()

        def post(path, data):
            response = request.post(BASE + path, data=data)
            assert response.ok and response.json().get('ok', True), response.text()
            return response.json()

        usb = get('/api/remote/usb')
        assert usb['connected'] and len(usb['devices']) == 2, usb
        original = get('/api/hdmi')
        mappings = usb['mappings']
        try:
            page.goto(BASE + '/admin', wait_until='domcontentloaded')
            page.get_by_role('button', name='REMOTE', exact=True).click()
            expect(page.locator('#usbRemoteStatus')).to_contain_text('Connected: 2 input interfaces')
            page.locator('#usbRemoteAction').select_option('PLAY_PAUSE')
            page.get_by_role('button', name='LEARN USB BUTTON', exact=True).click()
            expect(page.locator('#usbRemoteStatus')).to_contain_text('Press the USB button')
            assert get('/api/remote/usb')['learning'] == 'PLAY_PAUSE'
            page.get_by_role('button', name='CANCEL', exact=True).click()
            expect(page.locator('#usbRemoteStatus')).to_contain_text('Connected: 2 input interfaces')
            assert get('/api/remote/usb')['mappings'] == mappings
            page.screenshot(path='/tmp/retro-tv-usb-setup.png', full_page=True)
            print('USB receiver status and button-learning UI passed.', flush=True)

            page.goto(BASE + '/remote', wait_until='domcontentloaded')
            # Exercise the same backend navigation used by the hardware controller,
            # then take over from the phone without resetting its selection.
            post('/api/tv-guide', {})
            expect(page.locator('#guideButton')).to_have_text('EXIT GUIDE', timeout=10000)
            native = post('/api/tv-guide/nav', {'action': 'down'})
            expect(page.locator('#ncCh')).to_have_text(str(native['channel']).zfill(2), timeout=10000)
            with page.expect_response('**/api/tv-guide/nav') as navigation:
                page.get_by_role('button', name='Right', exact=True).click()
            assert navigation.value.json()['ok']
            guide = get('/api/hdmi')['tv_guide']
            assert guide['channel'] == native['channel']
            assert guide['entry']['id'] != native['entry']['id']
            post('/api/tv-guide/nav', {'action': 'back'})
            expect(page.locator('#guideButton')).to_have_text('GUIDE', timeout=10000)

            post('/api/tv-vod', {'action': 'open'})
            expect(page.locator('#vodButton')).to_have_text('EXIT VOD', timeout=10000)
            expect(page.locator('#ncLive')).to_have_text('ON DEMAND')
            page.get_by_role('button', name='Right', exact=True).click()
            post('/api/tv-vod', {'action': 'close'})
            expect(page.locator('#vodButton')).to_have_text('ON DEMAND', timeout=10000)
            # Live TV also clears a guide opened from another controller.
            post('/api/tv-guide', {})
            page.get_by_role('button', name='● LIVE TV', exact=True).click()
            expect(page.locator('#ncLive')).to_have_text('LIVE', timeout=10000)
            assert not get('/api/hdmi')['tv_guide']['visible']
            print('Native/phone guide and VOD handoff passed.', flush=True)
            assert not errors, errors
        finally:
            post('/api/remote/usb', {'action': None})
            post('/api/tv-vod', {'action': 'close'})
            post('/api/tv-guide', {'action': 'close'})
            if original['channel'] is not None:
                post('/api/tune', {'channel': original['channel']})
            browser.close()
    print('USB remote interoperability checks passed.')


if __name__ == '__main__':
    run()
