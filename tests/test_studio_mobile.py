"""Real browser regression checks; run with PLAYWRIGHT_BROWSERS_PATH configured.

Uses temporary project data and an isolated local server, never the live studio.
"""
import io
import threading
import time

import pytest
from PIL import Image

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def mobile_server(tmp_path, monkeypatch):
    import socket
    import uvicorn
    from studio import settings, store, planner
    from studio.app import app
    monkeypatch.setattr(settings, 'DATA_ROOT', str(tmp_path))
    monkeypatch.setattr(settings, 'SETTINGS_PATH', str(tmp_path / 'settings.json'))
    settings.save({'image': {'width': 640, 'height': 360}, 'style_defaults': {'default_engine': 'wan', 'default_frame_source': 'zimage'}})
    sid = store.create_series('Mobile Test')['id']
    eid = store.create_episode(sid, 'Pilot')['id']
    shot = planner.new_shot(id='S01_SH01', order=0)
    store.update_episode(sid, eid, lambda e: e['shots'].append(shot))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.05)
    yield f'http://127.0.0.1:{port}', sid, eid
    server.should_exit = True; thread.join(timeout=5)


def photo():
    image = Image.new('RGB', (1000, 800), 'red')
    image.paste(Image.new('RGB', (1000, 400), 'blue'), (0,400))
    data = io.BytesIO(); image.save(data, format='JPEG')
    return {'name': 'phone.jpg', 'mimeType': 'image/jpeg', 'buffer': data.getvalue()}


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
def test_touch_crop_upload_and_mobile_layout(mobile_server, engine, tmp_path):
    from studio import store
    base,sid,eid = mobile_server
    with playwright.sync_playwright() as p:
        browser = getattr(p, engine).launch(headless=True)
        context = browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        page = context.new_page(); errors=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/status', lambda route: route.fulfill(json={'comfy':False,'comfy_host':'local','ollama_models':[],'loras':[],'missing':[]}))
        page.goto(f'{base}/#/s/{sid}/e/{eid}/frames')
        with page.expect_file_chooser() as chooser:
            page.locator('[data-upframe]').tap()
        chooser.value.set_files(photo())
        page.wait_for_function('!document.querySelector("#ck").disabled')
        page.locator('#cs').fill('50')
        before = page.locator('#cb').bounding_box()
        box = page.locator('#cb')
        if engine == 'chromium':
            session=context.new_cdp_session(page)
            session.send('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[{'x':before['x']+20,'y':before['y']+20}]})
            session.send('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[{'x':before['x']+45,'y':before['y']+45}]})
            session.send('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
        else:
            page.mouse.move(before['x']+20,before['y']+20);page.mouse.down()
            page.mouse.move(before['x']+45,before['y']+45,steps=5);page.mouse.up()
        after=box.bounding_box()
        assert after['x'] > before['x'] and after['y'] > before['y']
        # Background job completion must not discard the active crop.
        page.route('**/api/jobs', lambda route: route.fulfill(json=[{'id':'done','status':'done','title':'Test','progress':[1,1],'log':[],'error':''}]))
        page.evaluate('App.lastJobs.done="running"; pollJobs()')
        assert page.locator('#ck').is_visible()
        page.set_viewport_size({'width':844,'height':390})
        page.wait_for_timeout(100)
        playwright.expect(page.locator('#ck')).to_be_in_viewport()
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(300)
        page.locator('#ck').tap()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(tmp_path/'crop-result.png'))
        page.get_by_text('Uploaded and selected.', exact=True).wait_for(timeout=5000)
        episode=store.get_episode(sid,eid)
        assert len(episode['shots'][0]['frame']['candidates']) == 1
        assert episode['shots'][0]['frame']['selected'] == 0
        saved=Image.open(store.abs_media(episode['shots'][0]['frame']['candidates'][0]['file']))
        assert saved.getpixel((320,180))[2] > 200
        # Cancelling resolves the upload and allows the exact same photo to be chosen again.
        page.locator('[data-upframe]').set_input_files(photo())
        page.locator('#cn').tap()
        page.wait_for_function('!App.interactionBusy',timeout=5000)
        page.locator('[data-upframe]').set_input_files(photo())
        page.locator('#cn').wait_for()
        page.keyboard.press('Escape')
        page.wait_for_function('!App.interactionBusy',timeout=5000)
        page.locator('[data-upend]').set_input_files(photo())
        page.wait_for_function('!document.querySelector("#ck").disabled')
        page.locator('#ck').tap(); page.get_by_text('End frame set.', exact=True).wait_for()
        assert store.get_episode(sid,eid)['shots'][0]['end_frame']
        from pillow_heif import from_pillow
        heic=tmp_path/'phone.heic';from_pillow(Image.new('RGB',(200,160),'green')).save(heic)
        page.locator('[data-upframe]').set_input_files(str(heic))
        page.wait_for_function('!document.querySelector("#ck").disabled')
        page.locator('#ck').tap()
        page.wait_for_function('!App.interactionBusy')
        assert len(store.get_episode(sid,eid)['shots'][0]['frame']['candidates'])==2
        for width in [320,390,768,1440]:
            page.set_viewport_size({'width':width,'height':844})
            for tab in ['story','shots','frames','takes','cut']:
                page.goto(f'{base}/#/s/{sid}/e/{eid}/{tab}')
                page.locator('#tab').wait_for()
                page.wait_for_timeout(50)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), (engine,width,tab)
        page.set_viewport_size({'width':390,'height':844})
        page.evaluate('setCollapsed(true)')
        page.locator('#menu-btn').tap()
        playwright.expect(page.locator('#nav').get_by_text('Settings',exact=True)).to_be_visible()
        page.locator('#menu-backdrop').tap(position={'x':370,'y':200})
        assert not page.evaluate('document.querySelector("#app").classList.contains("menu")')
        page.screenshot(path=str(tmp_path/f'{engine}-mobile.png'))
        assert not errors, errors
        browser.close()


def test_installable_shell_and_offline_recovery(mobile_server):
    import requests
    base,sid,eid=mobile_server
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        page=context.new_page()
        page.goto(base)
        page.evaluate('navigator.serviceWorker.ready')
        page.wait_for_function('navigator.serviceWorker.controller !== null')
        assert page.evaluate('window.isSecureContext')
        manifest=page.evaluate('fetch(document.querySelector("link[rel=manifest]").href).then(r=>r.json())')
        assert manifest['display']=='standalone'
        page.evaluate('fetch("/api/series").then(r=>r.json())')
        cached=page.evaluate('caches.keys().then(keys=>Promise.all(keys.map(k=>caches.open(k).then(c=>c.keys().then(rs=>rs.map(r=>r.url))))))')
        assert not any('/api/' in url or '/media/' in url for group in cached for url in group)
        context.set_offline(True)
        page.reload()
        page.get_by_role('heading',name='Your studio is offline').wait_for()
        context.set_offline(False)
        page.get_by_role('link',name='Try again').click()
        page.get_by_role('heading',name='Projects',exact=True).wait_for()
        assert len(requests.get(base+'/api/series').json())==1
        browser.close()


def test_character_reference_correction_model_settings(mobile_server):
    from studio import settings
    base, sid, eid = mobile_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/status', lambda route: route.fulfill(json={'comfy': False, 'comfy_host': 'local',
                    'ollama_models': [], 'loras': [], 'missing': []}))
        page.goto(f'{base}/#/models')
        page.locator('#face-model').select_option('minimax_identity')
        page.get_by_text('Local character reference files', exact=True).tap()
        page.locator('#identity-unet').fill('custom-ref2va.safetensors')
        page.locator('#save-models').tap()
        page.get_by_text('Model settings saved.', exact=True).wait_for()
        cfg = settings.load()['face_correction']
        assert cfg['model'] == 'minimax_identity'
        assert cfg['identity_unet'] == 'custom-ref2va.safetensors'
        assert cfg['identity_lora'] == 'h3_character_swap_v1.5.safetensors'
        page.goto(f'{base}/#/s/{sid}/e/{eid}/takes')
        assert page.locator('#face-S01_SH01').input_value() == 'minimax_identity'
        assert not errors, errors
        browser.close()
