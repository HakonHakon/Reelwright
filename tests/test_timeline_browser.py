"""Exercise actual editor controls and persistence in Android/iOS browser engines."""
import socket
import threading
import time

import pytest
import uvicorn

from studio import store
from studio.app import app
from test_lipsync import film

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def edit_server(film, monkeypatch):
    client, sid, eid, shots, music = film
    submitted = []
    def submit(kind, title, fn, **kw):
        submitted.append({'kind':kind, **kw})
        return {'id':'test', 'title':title}
    monkeypatch.setattr('studio.app._submit', submit)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread = threading.Thread(target=server.run,daemon=True); thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    yield f'http://127.0.0.1:{port}',sid,eid,shots,music,submitted
    server.should_exit=True; thread.join(timeout=5)


@pytest.mark.parametrize('engine,mobile',[('chromium',False),('chromium',True),('webkit',True)])
def test_timeline_edit_music_generate_and_export(edit_server,engine,mobile,tmp_path):
    base,sid,eid,shots,music,submitted=edit_server
    with playwright.sync_playwright() as p:
        browser=getattr(p,engine).launch(headless=True)
        context=browser.new_context(viewport={'width':390 if mobile else 1440,'height':844 if mobile else 1100},
                                    is_mobile=mobile,has_touch=mobile,service_workers='block')
        page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/api/status',lambda r:r.fulfill(json={'comfy':False,'comfy_host':'local','ollama_models':[],'loras':[],'missing':[]}))
        page.goto(f'{base}/#/s/{sid}/e/{eid}/takes')
        page.locator('.edit-clip').first.wait_for()
        assert page.locator('.edit-clip').count()==2
        assert 'timeline' not in store.get_episode(sid,eid)
        # Split exactly in half, mute the new segment, reorder, delete and undo.
        page.locator('[data-edit-scrub]').fill('15')
        page.locator('[data-edit="split"]').click()
        playwright.expect(page.locator('.edit-clip')).to_have_count(3)
        if mobile:page.locator('[data-edit="inspector"]').click()
        page.locator('[data-edit-muted]').check()
        page.locator('[data-edit="later"]').click()
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        edit=store.get_episode(sid,eid)['timeline']
        assert [c['frames'] for c in edit['clips']]==[15,30,15]
        assert edit['clips'][2]['source_in']==.5 and edit['clips'][2]['muted']
        page.locator('[data-edit="delete"]').click()
        playwright.expect(page.locator('.edit-clip')).to_have_count(2)
        page.locator('[data-edit="undo"]').click()
        playwright.expect(page.locator('.edit-clip')).to_have_count(3)
        # Touch edge trim (Android) and pointer drag (desktop/iOS engine).
        page.locator('[data-edit-zoom]').fill('160')
        edge=page.locator('.edit-clip').last.locator('.edit-edge.left')
        edge.scroll_into_view_if_needed(); box=edge.bounding_box()
        x,y=box['x']+5,box['y']+40
        if mobile and engine=='chromium':
            session=context.new_cdp_session(page)
            session.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
            session.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x+16,'y':y}]})
            session.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
        else:
            page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+16,y,steps=5);page.mouse.up()
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        edit=store.get_episode(sid,eid)['timeline']
        assert edit['clips'][2]['frames']==12 and edit['clips'][2]['source_in']==.6
        # Grips reorder clips, and the waveform can move independently.
        grip=page.locator('.edit-clip').first.locator('.edit-grip')
        grip.scroll_into_view_if_needed();box=grip.bounding_box()
        x,y=box['x']+box['width']/2,box['y']+box['height']/2
        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+160,y,steps=8);page.mouse.up()
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        assert store.get_episode(sid,eid)['timeline']['clips'][0]['shot_id']==shots[1]['id']
        page.locator('[data-edit="undo"]').click()
        # Upload recording, opt into vocal lip-sync and choose both characters.
        page.locator('[data-edit-audio]').set_input_files(str(music))
        page.locator('#ask-yes').click()
        page.locator('#edit-sync-all').click()
        page.locator('#edit-sync-apply').click()
        page.locator('[data-music-start]').fill('.2')
        page.locator('[data-music-start]').press('Tab')
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        edit=store.get_episode(sid,eid)['timeline']
        assert edit['music']['start_frame']==6
        assert all(c['lipsync'] and c['muted'] for c in edit['clips'])
        assert page.locator('.edit-music-clip svg polygon').count()==1
        waveform=page.locator('.edit-music-clip');waveform.scroll_into_view_if_needed();box=waveform.bounding_box()
        x,y=box['x']+20,box['y']+20
        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+16,y,steps=5);page.mouse.up()
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        assert store.get_episode(sid,eid)['timeline']['music']['start_frame']==9
        page.locator('[data-edit="undo"]').click()
        # Replacing a song requires fresh lip-sync consent; undo restores choices.
        page.locator('[data-edit-audio]').set_input_files(str(music))
        page.locator('#ask-no').click()
        assert page.evaluate('App.timelineEditor.edit.clips.every(c=>!c.lipsync)')
        page.locator('[data-edit="undo"]').click()
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        edit=store.get_episode(sid,eid)['timeline']
        # Generation is a Studio job using the selected clip's shot/model.
        select=page.locator('[data-edit-model]')
        value=select.locator('option').nth(1).get_attribute('value')
        select.select_option(value)
        page.locator('[data-edit="generate"]').click()
        page.wait_for_function('document.querySelector("#toasts").textContent.includes("Queued:")')
        assert submitted[-1]['kind']=='takes' and submitted[-1]['shot_ids']==[shots[0]['id']]
        assert submitted[-1]['model_override']==value
        # Export receives the saved edit; no model work is launched by this test.
        page.locator('[data-edit="export"]').click()
        page.wait_for_function('!App.timelineEditor.saving')
        playwright.expect(page.locator('#toasts')).to_contain_text('Timeline export')
        assert submitted[-1]['kind']=='timeline' and submitted[-1]['edit']==edit
        # Source library is retained and can append another candidate.
        page.locator('.take-library-panel > summary').click()
        page.locator('[data-timeline-add]').last.click()
        playwright.expect(page.locator('.edit-clip')).to_have_count(4)
        page.locator('[data-edit="undo"]').click()
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        # Expanded mobile editor contains its own scroll area; page does not overflow.
        page.locator('[data-edit="focus"]').click()
        assert page.locator('.take-editor.focused').is_visible()
        # Dialogs must stay usable above the expanded editor.
        page.locator('[data-edit="sync"]').click()
        page.locator('#edit-sync-all').click()
        page.locator('#edit-sync-apply').click()
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
        page.screenshot(path=str(tmp_path/f'{engine}-{mobile}-timeline.png'))
        page.keyboard.press('Escape')
        page.reload();page.locator('.edit-clip').first.wait_for()
        assert page.locator('.edit-clip').count()==3
        assert page.locator('[data-edit-time]').inner_text()=='0.000 / 1.900 s'
        # Scrubbing/playback follows trimmed clip timing and its audio position.
        page.locator('[data-edit-scrub]').fill('45')
        page.wait_for_function('document.querySelector("[data-edit-video]").readyState>=2')
        page.wait_for_function('Math.abs(document.querySelector("[data-edit-video]").currentTime-.6)<.05')
        page.locator('[data-edit="start"]').click()
        page.locator('[data-edit="play"]').click()
        page.wait_for_function('Number(document.querySelector("[data-edit-scrub]").value)>10')
        page.locator('[data-edit="play"]').click()
        page.goto(f'{base}/#/s/{sid}/e/{eid}/cut')
        page.locator('#render-edit').wait_for()
        assert page.get_by_text('Edit clips, music and lip-sync in Takes',exact=True).is_visible()
        assert not errors,errors
        browser.close()


def _drag(page, locator, dx, hold_alt=False):
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    if hold_alt:
        page.keyboard.down('Alt')
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + dx, y, steps=6); page.mouse.up()
    if hold_alt:
        page.keyboard.up('Alt')


def test_pro_timeline_controls(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/status', lambda r: r.fulfill(json={'comfy': False, 'comfy_host': 'local', 'ollama_models': [], 'loras': [], 'missing': []}))
        page.goto(f'{base}/#/s/{sid}/e/{eid}/takes')
        page.locator('.edit-clip').first.wait_for()
        ed = lambda expr: page.evaluate(f'App.timelineEditor.{expr}')
        assert ed('total') == 60 and ed('fps') == 30

        # Keyboard: frame step, second step, cut jumps, home/end, with a frame-accurate timecode.
        page.locator('.take-editor').focus()
        page.keyboard.press('ArrowRight')
        assert ed('frame') == 1 and page.locator('[data-edit-tc]').inner_text() == '00:00:00:01'
        page.keyboard.press('Shift+ArrowRight')
        assert ed('frame') == 31 and page.locator('[data-edit-tc]').inner_text() == '00:00:01:01'
        page.keyboard.press('ArrowUp');   assert ed('frame') == 30
        page.keyboard.press('ArrowDown'); assert ed('frame') == 59
        page.keyboard.press('Home');      assert ed('frame') == 0
        page.keyboard.press('End');       assert ed('frame') == 59
        assert page.locator('[data-edit-time]').inner_text() == '1.967 / 2.000 s'

        # Split, then undo, from the keyboard.
        page.locator('[data-edit-scrub]').fill('15')
        page.locator('.take-editor').focus()
        page.keyboard.press('s')
        playwright.expect(page.locator('.edit-clip')).to_have_count(3)
        page.keyboard.press('Control+z')
        playwright.expect(page.locator('.edit-clip')).to_have_count(2)

        # Trim start/end to the playhead (Q / W) on the selected first clip.
        page.locator('.edit-clip').first.click()
        page.locator('[data-edit-scrub]').fill('10')
        page.locator('.take-editor').focus()
        page.keyboard.press('q')
        assert ed('edit.clips[0].frames') == 20 and abs(ed('edit.clips[0].source_in') - 10 / 30) < 1e-6
        page.keyboard.press('w')
        assert ed('edit.clips[0].frames') == 10
        page.keyboard.press('Control+z'); page.keyboard.press('Control+z')
        assert ed('edit.clips[0].frames') == 30 and ed('edit.clips[0].source_in') == 0

        # Per-clip mute shows a badge; track mute buttons toggle everything.
        page.locator('.edit-clip').first.click(); page.keyboard.press('m')
        assert page.locator('.edit-clip .edit-badges i').count() == 1
        page.locator('[data-edit="track-video-mute"]').click()
        assert page.locator('[data-edit="track-video-mute"]').get_attribute('aria-pressed') == 'true'
        assert ed('edit.clips.every(c => c.muted)')
        page.locator('[data-edit="track-video-mute"]').click()
        assert not ed('edit.clips.some(c => c.muted)')
        has_music = ed('edit.music != null')
        music_mute = page.locator('[data-edit="track-music-mute"]')
        assert music_mute.is_disabled() == (not has_music)
        if has_music:
            music_mute.click(); assert ed('edit.music.muted') is True and music_mute.get_attribute('aria-pressed') == 'true'
            music_mute.click(); assert ed('edit.music.muted') is False

        # Snap toggles from the keyboard and is remembered; Fit shows the whole timeline.
        assert ed('snap') is True
        page.keyboard.press('n'); assert ed('snap') is False
        assert page.locator('[data-edit="snap"]').get_attribute('aria-pressed') == 'false'
        page.keyboard.press('n'); assert ed('snap') is True
        page.locator('[data-edit="zoom-fit"]').click()
        assert page.evaluate('(() => { const s = document.querySelector(".edit-scroll"); return s.scrollWidth <= s.clientWidth + 1; })()')
        assert 4 <= ed('zoom') <= 400

        # Dragging the ruler scrubs the playhead.
        page.locator('[data-edit-zoom]').fill('48')
        page.evaluate('document.querySelector(".edit-scroll").scrollLeft = 0')
        page.locator('.edit-ruler').scroll_into_view_if_needed()
        box = page.locator('.edit-ruler').bounding_box()
        page.mouse.move(box['x'] + 2, box['y'] + 10); page.mouse.down()
        page.mouse.move(box['x'] + 48, box['y'] + 10, steps=5); page.mouse.up()
        assert ed('frame') == 30

        # Snapping: a trim dragged near the playhead lands on it; Alt bypasses the magnet.
        page.locator('[data-edit-zoom]').fill('160')
        page.locator('[data-edit-scrub]').fill('20')
        page.locator('.edit-clip').first.click()
        edge = page.locator('.edit-clip').first.locator('.edit-edge.right')
        _drag(page, edge, -48)
        assert ed('edit.clips[0].frames') == 20, 'trim should snap to the playhead at frame 20'
        page.locator('[data-edit="undo"]').click()
        _drag(page, edge, -48, hold_alt=True)
        assert ed('edit.clips[0].frames') == 21, 'Alt must bypass snapping'
        page.locator('[data-edit="undo"]').click()
        assert ed('snapAt') is None and page.locator('.edit-snap-line').is_hidden()

        # Ctrl+wheel zooms around the pointer.
        before = ed('zoom')
        scroll = page.locator('.edit-scroll').bounding_box()
        prevented = page.evaluate("""([x, y]) => {
            const ev = new WheelEvent('wheel', {deltaY: -120, ctrlKey: true, clientX: x, clientY: y, bubbles: true, cancelable: true});
            document.querySelector('.edit-scroll').dispatchEvent(ev); return ev.defaultPrevented;
        }""", [scroll['x'] + 200, scroll['y'] + 60])
        assert prevented, 'Ctrl+wheel must not trigger browser page zoom'
        assert ed('zoom') > before
        plain = page.evaluate("""() => { const ev = new WheelEvent('wheel', {deltaY: 100, bubbles: true, cancelable: true}); document.querySelector('.edit-scroll').dispatchEvent(ev); return ev.defaultPrevented; }""")
        assert not plain, 'plain wheel must still scroll the page'
        assert not errors, errors
        browser.close()


def _open_editor(page, base, sid, eid):
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/api/status', lambda r: r.fulfill(json={'comfy': False, 'comfy_host': 'local', 'ollama_models': [], 'loras': [], 'missing': []}))
    page.goto(f'{base}/#/s/{sid}/e/{eid}/takes')
    page.locator('.edit-clip').first.wait_for()
    page.wait_for_function('!App.timelineEditor.edit.music || App.timelineEditor.waveform')  # the one late redraw
    return errors, (lambda expr: page.evaluate(f'App.timelineEditor.{expr}'))


def _split_at(page, frame):
    page.locator('[data-edit-scrub]').fill(str(frame))
    page.locator('.take-editor').focus()
    page.keyboard.press('s')


def test_multi_select_group_edits_and_copy_paste(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        _split_at(page, 15)
        assert ed('edit.clips.length') == 3
        page.locator('[data-edit-zoom]').fill('160')
        clips = page.locator('.edit-clip')

        # Shift selects a range, the summary and inspector describe the group.
        clips.nth(0).click(); clips.nth(2).click(modifiers=['Shift'])
        assert ed('selection.size') == 3
        assert page.locator('[data-edit-inspector] h3').first.inner_text() == '3 clips selected'
        assert page.locator('[data-edit-sel]').inner_text().startswith('Selected: 3 clips')
        assert clips.nth(1).get_attribute('aria-pressed') == 'true'

        # Group mute from the keyboard and from the inspector, group volume.
        page.keyboard.press('m'); assert ed('edit.clips.every(c => c.muted)')
        page.keyboard.press('m'); assert ed('edit.clips.every(c => !c.muted)')
        page.locator('[data-edit-muted]').check(); assert ed('edit.clips.every(c => c.muted)')
        page.locator('[data-edit-muted]').uncheck(); assert ed('edit.clips.every(c => !c.muted)')
        page.locator('[data-edit-volume]').fill('0.5'); assert ed('edit.clips.every(c => c.volume === 0.5)')

        # Ctrl toggles one clip; non-adjacent clips cannot be moved as a block.
        clips.nth(1).click(modifiers=['Control'])
        assert ed('selection.size') == 2 and not page.evaluate('(() => { const e = App.timelineEditor; return e.selection.has(e.edit.clips[1].id); })()')
        before = ed('edit.clips.map(c => c.id)')
        page.keyboard.press('Alt+ArrowRight')
        playwright.expect(page.locator('#toasts')).to_contain_text('neighbouring')
        assert ed('edit.clips.map(c => c.id)') == before

        # Ctrl+A, copy, paste after the first clip; copies get fresh ids and become the selection.
        page.keyboard.press('Control+a'); assert ed('selection.size') == 3
        page.keyboard.press('Control+c')
        clips.nth(0).click()
        page.keyboard.press('Control+v')
        playwright.expect(clips).to_have_count(6)
        assert page.evaluate('new Set(App.timelineEditor.edit.clips.map(c => c.id)).size') == 6
        assert page.evaluate('(() => { const e = App.timelineEditor; return e.edit.clips.slice(1, 4).every(c => e.selection.has(c.id)) && e.selection.size === 3; })()')
        assert ed('edit.clips.slice(1, 4).every(c => c.volume === 0.5)'), 'pasted clips keep their settings'

        # Group duplicate lands after the group; group delete removes exactly the group and undo restores it.
        page.keyboard.press('Control+d'); playwright.expect(clips).to_have_count(9)
        page.keyboard.press('Delete'); playwright.expect(clips).to_have_count(6)
        page.keyboard.press('Control+z'); playwright.expect(clips).to_have_count(9)

        # Contiguous groups move as one block.
        order = ed('edit.clips.map(c => c.id)')
        page.keyboard.press('Alt+ArrowLeft')
        moved = ed('edit.clips.map(c => c.id)')
        assert moved != order and sorted(moved) == sorted(order)

        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = store.get_episode(sid, eid)['timeline']['clips']
        assert len(saved) == 9 and [c['id'] for c in saved] == moved
        assert not errors, errors
        browser.close()


def test_slip_and_roll_tools(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        _split_at(page, 15)  # clips: A1 [src 0..15), A2 [src 15..30), B
        page.locator('[data-edit-zoom]').fill('160')
        total = ed('total')
        clips = page.locator('.edit-clip')

        # Slip: the clip keeps its place and length, but shows a different part of its take.
        page.locator('.take-editor').focus()
        page.keyboard.press('y'); assert ed('tool') == 'slip'
        assert page.locator('[data-edit="tool-slip"]').get_attribute('aria-pressed') == 'true'
        clips.nth(1).scroll_into_view_if_needed(); box = clips.nth(1).bounding_box(); x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 43, y, steps=6); page.mouse.up()
        assert ed('edit.clips[1].frames') == 15 and ed('total') == total
        assert ed('edit.clips[1].source_in') == pytest.approx(7 / 30), 'dragging right reveals earlier footage'
        clips.nth(1).scroll_into_view_if_needed(); box = clips.nth(1).bounding_box(); x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 600, y, steps=6); page.mouse.up()
        assert ed('edit.clips[1].source_in') == 0, 'slip stops at the start of the take'
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x - 900, y, steps=6); page.mouse.up()
        assert ed('edit.clips[1].source_in') == pytest.approx(15 / 30), 'slip stops when the clip would run out of footage'
        page.keyboard.press('Control+z'); page.keyboard.press('Control+z'); page.keyboard.press('Control+z')
        assert ed('edit.clips[1].source_in') == pytest.approx(15 / 30)

        # Roll: the cut between A1 and A2 moves; total length and the neighbours' outer edges do not.
        page.keyboard.press('r'); assert ed('tool') == 'roll'
        edge = clips.nth(0).locator('.edit-edge.right'); edge.scroll_into_view_if_needed(); box = edge.bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 27, y, steps=6); page.mouse.up()
        assert ed('edit.clips.map(c => c.frames)') == [20, 10, 30] and ed('total') == total
        assert ed('edit.clips[1].source_in') == pytest.approx(20 / 30)
        assert ed('edit.clips[0].source_in') == 0
        # A roll cannot go past the footage that exists: A2 may shrink to one frame, no further.
        edge = clips.nth(0).locator('.edit-edge.right'); edge.scroll_into_view_if_needed(); box = edge.bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 900, y, steps=8); page.mouse.up()
        assert ed('edit.clips[1].frames') == 1 and ed('edit.clips[0].frames') == 29 and ed('total') == total
        page.keyboard.press('Control+z'); page.keyboard.press('Control+z')
        assert ed('edit.clips.map(c => c.frames)') == [15, 15, 30]

        page.keyboard.press('v'); assert ed('tool') == 'select'
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        assert not errors, errors
        browser.close()


FAKE_BEATS = {'version': 1, 'duration': 2.0, 'tempo': 120.0, 'beats': [0.0, 0.5, 1.0, 1.5], 'strength': [1, .6, .8, .6],
              'bars': [0], 'bar_confidence': 2.0, 'meter': 4}


def test_beat_grid_snapping_and_cut_to_beat(edit_server, monkeypatch):
    base, sid, eid, shots, music, submitted = edit_server
    monkeypatch.setattr('studio.beats.analyze_file', lambda *a, **k: dict(FAKE_BEATS))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        grid = page.locator('.edit-beatgrid i')
        assert grid.count() == 0 and page.locator('[data-edit="beat-cut"]').is_disabled()

        # Turning Beats on finds the tempo, draws the grid with bar lines, and enables the beat tools.
        page.locator('[data-edit="beats"]').click()
        page.wait_for_function('App.timelineEditor.beatData !== null')
        assert 'Beats · 120 BPM' in page.locator('[data-edit="beats"]').inner_text()
        assert grid.count() == 4 and page.locator('.edit-beatgrid i.bar').count() == 1
        assert ed('beatFrames().map(b => b.frame)') == [0, 15, 30, 45]
        assert page.locator('[data-edit="beat-cut"]').is_enabled()
        assert page.evaluate('localStorage.getItem("rw-beats")') == '1'

        # Beat navigation, and half/double time.
        page.locator('.take-editor').focus()
        page.keyboard.press('.'); assert ed('frame') == 15
        page.keyboard.press('.'); assert ed('frame') == 30
        page.keyboard.press(','); assert ed('frame') == 15
        page.locator('[data-edit="beat-half"]').click(); assert ed('beatFrames().map(b => b.frame)') == [0, 30]
        page.locator('[data-edit="beat-double"]').click(); assert len(ed('beatFrames()')) == 7
        page.locator('[data-edit="beat-double"]').click(); assert ed('beatMode') == 'normal' and grid.count() == 4

        # Scrubbing snaps to beats; Alt bypasses the magnet.
        page.locator('[data-edit-zoom]').fill('160')
        page.evaluate('document.querySelector(".edit-scroll").scrollLeft = 0')
        ruler = page.locator('.edit-ruler').bounding_box(); y = ruler['y'] + 10
        x16 = ruler['x'] + 16 / 30 * 160
        page.mouse.move(x16, y); page.mouse.down(); page.mouse.up()
        assert ed('frame') == 15 and page.locator('.edit-snap-line').is_hidden()
        page.keyboard.down('Alt'); page.mouse.move(x16, y); page.mouse.down(); page.mouse.up(); page.keyboard.up('Alt')
        assert ed('frame') == 16

        # Make an off-beat cut at frame 20. A trim dragged near a beat lands on it; Alt bypasses the magnet.
        _split_at(page, 20)
        assert ed('edit.clips.map(c => c.frames)') == [20, 10, 30]
        def drag_first_clip_end(dx, alt=False):
            edge = page.locator('.edit-clip').nth(0).locator('.edit-edge.right'); edge.scroll_into_view_if_needed(); box = edge.bounding_box()
            x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
            if alt: page.keyboard.down('Alt')
            page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + dx, y, steps=6); page.mouse.up()
            if alt: page.keyboard.up('Alt')
        drag_first_clip_end(-21)           # four frames left of the cut: nearest beat is 15
        assert ed('edit.clips[0].frames') == 15, 'trim should snap to the beat at frame 15'
        page.keyboard.press('Control+z'); assert ed('edit.clips[0].frames') == 20
        drag_first_clip_end(-21, alt=True)
        assert ed('edit.clips[0].frames') == 16, 'Alt bypasses beat snapping'
        page.keyboard.press('Control+z'); assert ed('edit.clips.map(c => c.frames)') == [20, 10, 30]

        # Move the off-beat cut onto the beat from the dialog (one undo step).
        page.locator('[data-edit="beat-cut"]').click()
        preview = page.locator('#bc-preview')
        playwright.expect(preview).to_contain_text('1 cut moved onto the beat')
        playwright.expect(preview).to_contain_text('1 already on a beat')
        page.locator('#bc-apply').click()
        assert ed('edit.clips.map(c => c.frames)') == [15, 15, 30] and ed('total') == 60
        assert ed('edit.clips[1].source_in') == pytest.approx(15 / 30)
        page.keyboard.press('Control+z')
        assert ed('edit.clips.map(c => c.frames)') == [20, 10, 30], 'the whole operation is one undo step'

        # Add cuts on the grid: align first, then split the last clip on its mid-beat.
        page.locator('[data-edit="beat-cut"]').click()
        page.locator('#bc-split').check(); page.locator('#bc-every').select_option('1')
        playwright.expect(page.locator('#bc-preview')).to_contain_text('1 new cut added')
        page.locator('#bc-apply').click()
        assert ed('edit.clips.map(c => c.frames)') == [15, 15, 15, 15] and ed('total') == 60
        assert ed('edit.clips[3].source_in') == pytest.approx(ed('edit.clips[2].source_in') + 15 / 30)
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        assert [c['frames'] for c in store.get_episode(sid, eid)['timeline']['clips']] == [15, 15, 15, 15]
        assert not errors, errors
        browser.close()


def test_failed_beat_analysis_turns_beats_off_cleanly(edit_server, monkeypatch):
    base, sid, eid, shots, music, submitted = edit_server
    def boom(*a, **k):
        raise ValueError('Choose a playable audio file')
    monkeypatch.setattr('studio.beats.analyze_file', boom)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('[data-edit="beats"]').click()
        page.wait_for_function('!App.timelineEditor.beatLoading && App.timelineEditor.beatFailed')
        assert ed('beatsOn') is False and page.locator('[data-edit="beat-cut"]').is_disabled()
        assert page.locator('[data-edit="beats"]').get_attribute('aria-pressed') == 'false'
        assert page.locator('.edit-beatgrid i').count() == 0
        browser.close()


def test_clip_filmstrip_thumbnails(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('[data-edit-zoom]').fill('160')
        tiles = page.locator('.edit-clip .edit-thumbs i')
        assert tiles.count() >= 2
        style = tiles.first.evaluate('el => getComputedStyle(el).backgroundImage')
        url = style.split('url("')[1].split('")')[0]
        assert '/timeline/thumb?file=' in url
        response = page.request.get(url)
        assert response.status == 200 and response.headers['content-type'] == 'image/jpeg'
        # Zooming out to fit keeps the strips; dragging does not break them.
        page.locator('[data-edit="zoom-fit"]').click()
        assert page.locator('.edit-clip .edit-thumbs i').count() >= 2
        assert not errors, errors
        browser.close()


def _drag_by(page, locator, dx, alt=False):
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    if alt:
        page.keyboard.down('Alt')
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + dx, y, steps=6); page.mouse.up()
    if alt:
        page.keyboard.up('Alt')


def _go(page, frame):
    page.locator('[data-edit-scrub]').fill(str(frame))
    page.locator('.take-editor').focus()


def test_markers_add_jump_rename_recolour_drag_snap_and_delete(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        flags = page.locator('.edit-marker')
        marks = lambda: page.evaluate('App.timelineEditor.markers.map(m => [m.frame, m.label, m.color])')

        # Shift+M adds a marker at the playhead, and again at the same frame removes it.
        _go(page, 10); page.keyboard.press('Shift+M')
        assert marks() == [[10, 'Marker 1', 'blue']] and flags.count() == 1
        assert page.locator('[data-edit="markers"]').inner_text() == 'Markers (1)'
        page.keyboard.press('Shift+M'); assert marks() == [] and flags.count() == 0
        page.keyboard.press('Shift+M'); _go(page, 40); page.keyboard.press('Shift+M')
        assert [m[0] for m in marks()] == [10, 40]

        # Alt+Up/Down jump between markers and select them; clicking a flag also seeks.
        page.keyboard.press('Alt+ArrowUp'); assert ed('frame') == 10 and flags.first.get_attribute('class').count('sel') == 1
        page.keyboard.press('Alt+ArrowDown'); assert ed('frame') == 40
        _go(page, 0); flags.first.click(); assert ed('frame') == 10
        page.wait_for_timeout(500)                  # a double-click is two taps, not three

        # Double-click opens the list focused on that marker; rename, recolour, delete (one undo step each).
        flags.first.dblclick()
        rows = page.locator('#mk-list .mk-row'); assert rows.count() == 2
        label = page.locator('#mk-list [data-mk-label]').first
        label.fill('Chorus'); label.press('Tab')
        assert marks()[0][1] == 'Chorus' and flags.first.inner_text() == 'Chorus'
        page.locator('#mk-list [data-mk-color]').first.select_option('red')
        assert marks()[0][2] == 'red' and 'c-red' in flags.first.get_attribute('class')
        assert '00:00:00:10  Chorus' in page.locator('#mk-text').input_value()
        page.locator('#mk-list [data-mk-del]').nth(1).click(); assert len(marks()) == 1 and rows.count() == 1
        page.locator('#mk-done').click(); page.wait_for_selector('#modal', state='hidden')
        page.keyboard.press('Control+z'); assert len(marks()) == 2, 'undo brings the deleted marker back'

        # A selected marker takes the Delete key; a selected clip takes it otherwise.
        clips_before = ed('edit.clips.length')
        flags.last.click(); page.keyboard.press('Delete')
        assert len(marks()) == 1 and ed('edit.clips.length') == clips_before
        page.locator('.edit-clip').first.click(); page.keyboard.press('Delete')
        assert ed('edit.clips.length') == clips_before - 1 and len(marks()) == 1
        page.keyboard.press('Control+z'); assert ed('edit.clips.length') == clips_before
        page.keyboard.press('Control+z'); assert len(marks()) == 2

        # Dragging a marker snaps to a cut (frame 30); Alt bypasses. Trims snap to markers too.
        page.locator('[data-edit-zoom]').fill('160'); _go(page, 59)
        _drag_by(page, flags.last, -48)            # 40 -> 31, snaps to the cut at 30
        assert marks()[-1][0] == 30
        page.keyboard.press('Control+z'); assert marks()[-1][0] == 40
        _drag_by(page, flags.last, -48, alt=True)
        assert marks()[-1][0] == 31
        page.keyboard.press('Control+z')
        page.keyboard.press('Control+z')           # remove the later marker so a clean one is at 10
        edge = page.locator('.edit-clip').first.locator('.edit-edge.right')
        _go(page, 59); page.keyboard.press('Shift+M'); page.keyboard.press('Control+z')
        before = ed('edit.clips[0].frames')
        assert before == 30
        page.evaluate('App.timelineEditor.changeEdit(() => { App.timelineEditor.edit.markers = [{id: "t", frame: 20, label: "T", color: "green"}]; })')
        _drag_by(page, edge, -48)                  # right edge 30 -> 21, snaps to the marker at 20
        assert ed('edit.clips[0].frames') == 20

        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        assert [(m['frame'], m['label'], m['color']) for m in store.get_episode(sid, eid)['timeline']['markers']] == [(20, 'T', 'green')]
        page.reload(); page.locator('.edit-clip').first.wait_for()
        assert page.locator('.edit-marker').count() == 1 and page.locator('.edit-marker').first.inner_text() == 'T'
        assert not errors, errors
        browser.close()


def test_render_range_in_out_handles_snapping_and_preview_request(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        rng = lambda: page.evaluate('App.timelineEditor.range')
        view = page.locator('.edit-rangeview')
        assert view.is_hidden() and page.locator('[data-edit="export-range"]').is_disabled() and page.locator('[data-edit="range-clear"]').is_disabled()

        # I and O set the range from the playhead; a missing end defaults to the end of the timeline.
        _go(page, 15); page.keyboard.press('i'); assert rng() == {'start': 15, 'end': 60}
        _go(page, 45); page.keyboard.press('o'); assert rng() == {'start': 15, 'end': 45}
        assert view.is_visible() and '0.50 → 1.50 s · 1.00 s long' in page.locator('[data-edit-range-info]').inner_text()
        _go(page, 5); page.keyboard.press('o'); assert rng() == {'start': 0, 'end': 5}, 'an end before the start restarts from 0'
        _go(page, 59); page.keyboard.press('o'); assert rng()['end'] == 60, 'Out on the last frame includes it'
        _go(page, 0); page.keyboard.press('o')
        playwright.expect(page.locator('#toasts')).to_contain_text('at least one frame')
        page.keyboard.press('Control+z'); page.keyboard.press('Control+z'); page.keyboard.press('Control+z')
        _go(page, 15); page.keyboard.press('i'); _go(page, 45); page.keyboard.press('o'); assert rng() == {'start': 15, 'end': 45}

        # Render range asks for exactly that span as a preview; the normal render is still the whole film.
        page.locator('[data-edit="export-range"]').click()
        page.wait_for_function('document.querySelector("#toasts").textContent.includes("Queued: Timeline preview")')
        assert submitted[-1]['kind'] == 'timeline' and submitted[-1]['window'] == (15, 45)
        page.locator('[data-edit="export"]').click()
        page.wait_for_function('App.timelineEditor && !App.timelineEditor.saving')
        page.wait_for_function('document.querySelector("#toasts").textContent.includes("Queued: Timeline export")')
        assert submitted[-1]['window'] is None

        # Handles drag with snapping (to the cut at frame 30); Alt bypasses.
        page.locator('[data-edit-zoom]').fill('160'); _go(page, 59)
        _drag_by(page, page.locator('.edit-rangeview .handle.in'), 14 * 160 / 30)   # 15 -> 29, snaps to the cut at 30
        assert rng() == {'start': 30, 'end': 45}
        page.keyboard.press('Control+z')
        _drag_by(page, page.locator('.edit-rangeview .handle.in'), 14 * 160 / 30, alt=True)
        assert rng()['start'] == 29
        page.keyboard.press('Control+z'); assert rng() == {'start': 15, 'end': 45}
        _drag_by(page, page.locator('.edit-rangeview .handle.out'), 40 * 160 / 30)   # cannot pass the end of the timeline
        assert rng()['end'] <= 60 and rng()['end'] > 45

        # Alt+X clears, undo restores; the range persists across a reload.
        page.keyboard.press('Alt+x'); assert rng() is None and view.is_hidden()
        page.keyboard.press('Control+z'); assert rng() is not None
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = store.get_episode(sid, eid)['timeline']['range']
        assert saved['start'] == 15 and saved['end'] > 45

        # Finished previews are listed beside the rendered timelines, without touching the final film.
        store.update_episode(sid, eid, lambda e: e['outputs'].update(timeline_previews=[{
            'file': 'preview.mp4', 'created': '2026-11-01 10:00', 'runtime': 1.0, 'revision': 3, 'video': {'fps': 30},
            'window': [15, 45], 'lipsync': False, 'lipsync_skipped': True, 'frames': 30}]))
        page.reload(); page.locator('.edit-clip').first.wait_for()
        summary = page.locator('.edit-results summary')
        assert summary.inner_text() == 'Range previews (1)'
        summary.click()
        text = page.locator('.edit-results').inner_text()
        assert 'range 0.50–1.50 s' in text and 'lip-sync skipped' in text
        assert page.locator('.edit-results a', has_text='Watch').get_attribute('href').endswith('preview.mp4')
        assert not errors, errors
        browser.close()


def test_fades_inspector_clamping_split_trim_group_and_persistence(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        fades = lambda: page.evaluate('App.timelineEditor.edit.clips.map(c => [c.frames, c.fade_in, c.fade_out])')
        page.locator('[data-edit-zoom]').fill('160')
        page.locator('.edit-clip').first.click()
        fade_in, fade_out = page.locator('[data-edit-fade="in"]'), page.locator('[data-edit-fade="out"]')

        fade_in.fill('0.2'); fade_in.press('Tab'); fade_out.fill('0.5'); fade_out.press('Tab')
        assert fades()[0] == [30, 6, 15]
        width = page.locator('.edit-clip').first.locator('.edit-fade.in').evaluate('el => el.getBoundingClientRect().width')
        assert abs(width - 6 / 30 * 160) < 1.5 and page.locator('.edit-clip').first.locator('.edit-fade.out').count() == 1

        # A fade longer than the clip is clamped, and the one you just edited wins.
        fade_in.fill('5'); fade_in.press('Tab')
        assert fades()[0] == [30, 30, 0]
        fade_in.fill('0.2'); fade_in.press('Tab'); fade_out.fill('0.5'); fade_out.press('Tab')
        assert fades()[0] == [30, 6, 15]

        # Splitting keeps the fade-in on the left piece and gives the fade-out to the right piece.
        _split_at(page, 15)
        assert fades()[:2] == [[15, 6, 0], [15, 0, 15]]
        # Trimming the right piece shorter than its fade-out shortens the fade instead of breaking the save.
        page.locator('[data-edit-scrub]').fill('28'); page.locator('.take-editor').focus(); page.keyboard.press('q')
        assert fades()[1] == [2, 0, 2]
        page.keyboard.press('Control+z'); assert fades()[1] == [15, 0, 15]

        # Several clips at once: a shared value is shown, mixed values show a placeholder.
        page.keyboard.press('Control+a')
        assert fade_in.input_value() == '' and fade_in.get_attribute('placeholder') == 'mixed'
        fade_in.fill('0.1'); fade_in.press('Tab')
        assert all(c[1] == 3 for c in fades()) and fade_in.input_value() == '0.10'

        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = [(c['frames'], c['fade_in'], c['fade_out']) for c in store.get_episode(sid, eid)['timeline']['clips']]
        assert saved[:2] == [(15, 3, 0), (15, 3, 12)]  # 3 + 15 would not fit a 15-frame clip
        assert not errors, errors
        browser.close()


def test_speed_inspector_split_continuity_lipsync_exclusion_and_persistence(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        state = lambda: page.evaluate('App.timelineEditor.edit.clips.map(c => [c.frames, c.speed, +c.source_in.toFixed(4)])')
        page.locator('.edit-clip').first.click()
        speed = page.locator('[data-edit-speed]')
        assert speed.input_value() == '1' and state()[0] == [30, 1, 0]

        # The clip keeps the same piece of source: half speed makes it twice as long, double speed half as long.
        speed.fill('0.5'); speed.press('Tab')
        assert state()[0] == [60, .5, 0]
        assert page.locator('.edit-clip').first.locator('.edit-clip-meta').inner_text().endswith('0.5×')
        assert page.locator('[data-edit-out]').input_value() == '1.000', 'source out is still the end of the 1 s source'
        speed.fill('2'); speed.press('Tab')
        assert state()[0] == [15, 2, 0]
        speed.fill('1.5'); speed.press('Tab')
        assert state()[0] == [20, 1.5, 0]
        speed.fill('9'); speed.press('Tab')           # out of range: refused, nothing changes
        assert state()[0] == [20, 1.5, 0]

        # Splitting at a timeline frame moves the source by (frames * speed): the picture stays continuous.
        _split_at(page, 10)
        assert state()[:2] == [[10, 1.5, 0], [10, 1.5, .5]]
        # The right piece already ends at the end of the source, so it cannot grow.
        assert page.evaluate("() => { const t = App.timelineEditor, c = t.edit.clips[1]; return t.fitFrames(c, t.sourceFrames(c) - Math.round(c.source_in * t.fps)); }") == 10
        page.keyboard.press('Control+z'); page.keyboard.press('Control+z'); page.keyboard.press('Control+z'); page.keyboard.press('Control+z')
        assert state()[0] == [30, 1, 0]

        # A sped-up clip cannot be lip-synced, and a lip-sync clip cannot change speed.
        speed.fill('2'); speed.press('Tab')
        assert page.locator('[data-edit-lipsync]').is_disabled()
        speed.fill('1'); speed.press('Tab')
        assert not page.locator('[data-edit-lipsync]').is_disabled()
        page.locator('[data-edit-lipsync]').check()
        assert speed.is_disabled()

        page.locator('[data-edit-lipsync]').uncheck()
        speed.fill('0.5'); speed.press('Tab')
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = store.get_episode(sid, eid)['timeline']['clips'][0]
        assert (saved['speed'], saved['frames'], saved['source_in']) == (.5, 60, 0)
        assert not errors, errors
        browser.close()


def test_audio_lanes_add_drag_mute_inspector_remove_and_persist(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        lanes = lambda: page.evaluate('App.timelineEditor.edit.audio.map(t => [t.start_frame, t.frames, t.muted, t.fade_in, t.fade_out, t.lane])')
        assert page.locator('.edit-lane-clip').count() == 0 and 'Audio lanes (0/8)' in page.locator('[data-edit-inspector]').inner_text()
        page.locator('[data-edit-zoom]').fill('100'); page.evaluate('App.timelineEditor.snap = false')

        # Adding audio puts a lane clip at the playhead, with its own label row and mute button; music is untouched.
        music_before = page.evaluate('JSON.stringify(App.timelineEditor.edit.music)')
        page.locator('[data-edit-scrub]').fill('6')
        page.locator('[data-edit-lane]').set_input_files(str(music))
        page.locator('.edit-lane-clip').first.wait_for()
        page.locator('[data-edit-scrub]').fill('0')
        page.locator('[data-edit-lane]').set_input_files(str(music))
        playwright.expect(page.locator('.edit-lane-clip')).to_have_count(2)
        assert page.evaluate('JSON.stringify(App.timelineEditor.edit.music)') == music_before
        assert page.locator('.lbl-lane').count() == 2 and [a[0] for a in lanes()] == [6, 0] and [a[5] for a in lanes()] == [0, 1]
        rows = page.evaluate("[...document.querySelectorAll('.edit-lane')].map(e => e.getBoundingClientRect().top)")
        labels = page.evaluate("[...document.querySelectorAll('.lbl-lane')].map(e => e.getBoundingClientRect().top)")
        assert all(abs(a - b) < 2 for a, b in zip(rows, labels)), 'labels line up with their lane rows'
        page.wait_for_function("document.querySelectorAll('.edit-lane-clip svg').length === 2")  # waveforms loaded

        # Dragging moves it in time (one second = 30 frames at this zoom) and is one undo step.
        page.locator('.edit-lane-clip').nth(1).scroll_into_view_if_needed()
        box = page.locator('.edit-lane-clip').nth(1).bounding_box()
        page.mouse.move(box['x'] + 20, box['y'] + 10); page.mouse.down(); page.mouse.move(box['x'] + 70, box['y'] + 10, steps=4); page.mouse.move(box['x'] + 120, box['y'] + 10, steps=4); page.mouse.up()
        assert lanes()[1][0] == 30, lanes()
        page.locator('.take-editor').focus(); page.keyboard.press('Control+z'); assert lanes()[1][0] == 0
        page.keyboard.press('Control+Shift+z'); assert lanes()[1][0] == 30

        # Mute from the label; the clip dims.
        page.locator('[data-edit="lane-mute"]').first.click()
        assert lanes()[0][2] is True and 'muted' in page.locator('.edit-lane-clip').first.get_attribute('class')

        # The inspector edits the second lane: start, fades (clamped to the length), then loop off/on.
        box2 = page.locator('[data-lane-box]').nth(1)
        start = box2.locator('[data-lane-field="start"]'); start.fill('0.5'); start.press('Tab')
        assert lanes()[1][0] == 15
        box2.locator('[data-lane-field="fade_in"]').fill('0.5'); box2.locator('[data-lane-field="fade_in"]').press('Tab')
        assert lanes()[1][3] == 15 and page.locator('.edit-lane-clip').nth(1).locator('.edit-fade.in').count() == 1
        box2.locator('[data-lane-field="length"]').fill('0.3'); box2.locator('[data-lane-field="length"]').press('Tab')
        assert lanes()[1][1] == 9 and lanes()[1][3] == 9, 'a shorter lane clip shortens its fade'
        too_long = box2.locator('[data-lane-field="length"]'); too_long.fill('9999'); too_long.press('Tab')
        assert lanes()[1][1] == 9, 'longer than the recording without Loop is refused'
        box2.locator('[data-lane-field="loop"]').check(); too_long = page.locator('[data-lane-box]').nth(1).locator('[data-lane-field="length"]')
        too_long.fill('9999'); too_long.press('Tab')
        assert lanes()[1][1] == 299970, lanes()

        # Autosave persists the lanes; removing one re-numbers the other.
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = store.get_episode(sid, eid)['timeline']['audio']
        assert [t['id'] for t in saved] == [t['id'] for t in page.evaluate('App.timelineEditor.edit.audio')] and saved[1]['loop'] is True
        page.locator('[data-lane-box]').first.locator('[data-edit="lane-remove"]').click()
        assert len(lanes()) == 1 and lanes()[0][5] == 0 and page.locator('.edit-lane-clip').count() == 1 and page.locator('.lbl-lane').count() == 1
        page.locator('.take-editor').focus(); page.keyboard.press('Control+z'); assert len(lanes()) == 2

        # Play: both lane players exist only while their clip is under the playhead.
        page.locator('[data-edit-scrub]').fill('20')
        assert page.evaluate('App.timelineEditor.laneEls.size') >= 1
        assert not errors, errors
        browser.close()


def test_crossfade_inspector_overlap_clamping_split_lipsync_and_persistence(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        clips = lambda: page.evaluate('App.timelineEditor.edit.clips.map(c => [c.frames, c.transition, App.timelineEditor.start(c.id)])')
        total = lambda: page.evaluate('App.timelineEditor.total')
        page.locator('[data-edit-zoom]').fill('100')
        assert clips() == [[30, 0, 0], [30, 0, 30]] and total() == 60

        # The first clip cannot be crossfaded.
        page.locator('.edit-clip').first.click()
        assert page.locator('[data-edit-xfade]').is_disabled()

        # A crossfade overlaps the clips: the second one starts earlier and the timeline gets shorter.
        page.locator('.edit-clip').nth(1).click()
        field = page.locator('[data-edit-xfade]')
        field.fill('0.3'); field.press('Tab')
        assert clips() == [[30, 0, 0], [30, 9, 21]] and total() == 51
        left = page.evaluate("[...document.querySelectorAll('.edit-clip')].map(e => e.getBoundingClientRect().left - document.querySelector('.edit-video-track').getBoundingClientRect().left)")
        assert abs(left[1] - 21 / 30 * 100) < 1.5, left
        assert abs(page.locator('.edit-clip').nth(1).locator('.edit-xfade').evaluate('e => e.getBoundingClientRect().width') - 30) < 1.5
        assert page.locator('[data-edit-scrub]').get_attribute('max') == '50'

        # More than half of the shorter clip is limited to half.
        field.fill('5'); field.press('Tab')
        assert clips()[1][1] == 15 and total() == 45
        field.fill('0.2'); field.press('Tab')
        assert clips()[1][1] == 6 and total() == 54

        # Splitting the incoming clip leaves the crossfade on the first piece only.
        _split_at(page, 40)
        assert [c[1] for c in clips()] == [0, 6, 0] and total() == 54
        # Cut-to-clip jumps land in the middle of the crossfade.
        assert page.evaluate('App.timelineEditor.cuts()')[:3] == [0, 27, 40]

        # Lip-sync cannot coexist with a crossfade next to it; turning it on drops the crossfade.
        page.locator('.edit-clip').nth(1).click()
        box = page.locator('[data-edit-lipsync]')
        assert box.is_enabled()
        box.check()
        assert clips()[1][1] == 0 and total() == 60

        page.keyboard.press('Control+z')
        page.locator('.take-editor').focus(); page.keyboard.press('Control+z')
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = [(c['frames'], c['transition']) for c in store.get_episode(sid, eid)['timeline']['clips']]
        assert saved[0] == (30, 0) and saved[1][1] == 6, saved
        assert not errors, errors
        browser.close()


def test_cut_to_beat_aligns_the_middle_of_a_crossfade_and_keeps_the_total(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.evaluate("() => { const t = App.timelineEditor; const [a, b] = t.edit.clips; a.frames = 24; Object.assign(b, {source_in: 10 / 30, frames: 20, transition: 8}); t.beatFrames = () => [{frame: 23, bar: true}]; }")
        plan = page.evaluate("""() => { const t = App.timelineEditor, r = t.planBeatCuts({align: true, tolerance: 0, every: 0, scope: 'all'});
            const {starts, total} = clipStarts(r.edit.clips); return {aligned: r.aligned, stuck: r.stuck, starts, total, frames: r.edit.clips.map(c => c.frames), middle: starts[1] + Math.floor(r.edit.clips[1].transition / 2)}; }""")
        assert plan['aligned'] == 1 and plan['stuck'] == 0, plan
        assert plan['middle'] == 23 and plan['total'] == 36 and plan['frames'] == [27, 17], plan
        # A beat that would make the crossfade longer than half of a clip is refused instead of moving things.
        page.evaluate("() => { const t = App.timelineEditor; t.beatFrames = () => [{frame: 25, bar: true}]; }")
        plan = page.evaluate("() => { const r = App.timelineEditor.planBeatCuts({align: true, tolerance: 0, every: 0, scope: 'all'}); return {aligned: r.aligned, stuck: r.stuck}; }")
        assert plan == {'aligned': 0, 'stuck': 1}, plan
        assert not errors, errors
        browser.close()


def test_overlay_lane_add_select_drag_inspector_preview_delete_and_persistence(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        overlays = lambda: page.evaluate('App.timelineEditor.edit.overlays.map(o => ({s: o.start_frame, n: o.frames, x: o.x, y: o.y, scale: o.scale, opacity: o.opacity, muted: o.muted, fi: o.fade_in, fo: o.fade_out}))')
        page.locator('[data-edit-zoom]').fill('100'); page.evaluate('App.timelineEditor.snap = false')
        assert page.locator('.edit-overlay-clip').count() == 0 and page.locator('.lbl-overlay').count() == 0

        # "Add as overlay" on a take card puts a clip on a new OVERLAY lane at the playhead, selected.
        page.locator('[data-edit-scrub]').fill('6')
        page.locator('.take-library-panel > summary').click()
        page.locator('[data-timeline-overlay]').last.click()
        page.locator('.edit-overlay-clip').first.wait_for()
        assert overlays() == [{'s': 6, 'n': 30, 'x': .75, 'y': .25, 'scale': .4, 'opacity': 1, 'muted': True, 'fi': 0, 'fo': 0}]
        assert page.locator('.lbl-overlay').count() == 1
        assert abs(page.locator('.lbl-overlay').bounding_box()['height'] - page.locator('.edit-overlay-track').bounding_box()['height']) < 2
        assert 'Overlay clip' in page.locator('[data-edit-inspector]').inner_text()
        assert page.evaluate('App.timelineEditor.total') == 60, 'overlays never change the timeline length'

        # Dragging moves it in time (100 px = 1 s = 30 frames) as one undo step.
        page.locator('.edit-overlay-clip').first.scroll_into_view_if_needed()
        box = page.locator('.edit-overlay-clip').first.bounding_box()
        page.mouse.move(box['x'] + 20, box['y'] + 10); page.mouse.down(); page.mouse.move(box['x'] + 70, box['y'] + 10, steps=4); page.mouse.move(box['x'] + 120, box['y'] + 10, steps=4); page.mouse.up()
        assert overlays()[0]['s'] == 36, overlays()
        page.locator('.take-editor').focus(); page.keyboard.press('Control+z'); assert overlays()[0]['s'] == 6
        page.keyboard.press('Control+Shift+z'); assert overlays()[0]['s'] == 36

        # Inspector: place, size, opacity, fades (clamped), mute, trim.
        def field(name): return page.locator(f'[data-ov-field="{name}"]')
        for name, value in [('x', '30'), ('y', '80'), ('scale', '55')]:
            field(name).fill(value); field(name).press('Tab')
        assert (overlays()[0]['x'], overlays()[0]['y'], overlays()[0]['scale']) == (.3, .8, .55)
        field('x').fill('250'); field('x').press('Tab'); assert overlays()[0]['x'] == 1, 'kept inside the frame'
        field('opacity').fill('0.5')
        field('fade_in').fill('0.5'); field('fade_in').press('Tab')
        field('length').fill('0.4'); field('length').press('Tab')
        assert overlays()[0]['n'] == 12 and overlays()[0]['fi'] == 12 and page.locator('.edit-overlay-clip .edit-fade.in').count() == 1, overlays()
        field('length').fill('99'); field('length').press('Tab'); assert overlays()[0]['n'] == 12, 'longer than the source video is refused'
        field('muted').uncheck(); assert overlays()[0]['muted'] is False

        # The monitor shows it while the playhead is inside, in the right place, and not outside.
        page.locator('[data-edit-scrub]').fill('40')
        page.wait_for_function("document.querySelectorAll('.edit-overlay-preview video').length === 1")
        style = page.locator('.edit-overlay-preview video').evaluate('v => [v.style.left, v.style.top, v.style.width]')
        assert style == ['100%', '80%', '55%'], style
        page.locator('[data-edit-scrub]').fill('2')
        page.wait_for_function("document.querySelectorAll('.edit-overlay-preview video').length === 0")

        # Autosave persists it; Delete removes the selected overlay and the lane label with it; undo brings it back.
        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = store.get_episode(sid, eid)['timeline']['overlays']
        assert len(saved) == 1 and saved[0]['start_frame'] == 36 and saved[0]['frames'] == 12 and saved[0]['scale'] == .55 and saved[0]['muted'] is False
        page.locator('.edit-overlay-clip').first.click()
        page.locator('.take-editor').focus(); page.keyboard.press('Delete')
        assert overlays() == [] and page.locator('.lbl-overlay').count() == 0 and page.locator('.edit-overlay-clip').count() == 0
        assert page.evaluate('App.timelineEditor.edit.clips.length') == 2, 'the main clips are untouched'
        page.keyboard.press('Control+z'); assert len(overlays()) == 1 and page.locator('.lbl-overlay').count() == 1
        assert not errors, errors
        browser.close()


def test_titles_dialog_lane_live_preview_delete_and_persistence(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        titles = lambda: page.evaluate('App.timelineEditor.titles.map(t => ({...t}))')
        preview = page.locator('.edit-title-preview .ttl')
        assert page.locator('[data-edit="titles"]').inner_text() == 'Titles (0)'

        # "Title" adds one at the playhead and opens the editor on its text.
        page.locator('[data-edit="title-add"]').click()
        field = page.locator('#tt-list [data-tt-text]'); field.wait_for()
        assert titles()[0]['frames'] == 60 and titles()[0]['start_frame'] == 0, 'defaults to 3 s, cut to the timeline'
        field.fill('Hello\nworld'); field.press('Tab')
        page.locator('[data-tt-start]').fill('0.5'); page.locator('[data-tt-start]').press('Tab')
        page.locator('[data-tt-length]').fill('1'); page.locator('[data-tt-length]').press('Tab')
        page.locator('[data-tt-x]').fill('20'); page.locator('[data-tt-x]').press('Tab')
        page.locator('[data-tt-y]').fill('30'); page.locator('[data-tt-y]').press('Tab')
        page.locator('[data-tt-size]').fill('10'); page.locator('[data-tt-size]').press('Tab')
        page.locator('[data-tt-color]').fill('#ff0000')
        page.locator('[data-tt-box]').uncheck()
        t = titles()[0]
        assert (t['text'], t['start_frame'], t['frames'], t['x'], t['y'], t['size'], t['color'], t['box']) == ('Hello\nworld', 15, 30, .2, .3, .1, '#ff0000', False)
        # nonsense is refused and the dialog shows the stored value again
        page.locator('[data-tt-length]').fill('0'); page.locator('[data-tt-length]').press('Tab')
        assert titles()[0]['frames'] == 30 and page.locator('[data-tt-length]').input_value() == '1.00'
        page.locator('#tt-done').click(); page.wait_for_selector('#modal', state='hidden')
        assert page.locator('[data-edit="titles"]').inner_text() == 'Titles (1)'
        block = page.locator('.edit-title'); assert block.count() == 1 and block.inner_text() == 'Hello world'

        # The preview shows the text only inside its frames, where and how it was set.
        page.locator('[data-edit-scrub]').fill('5'); assert preview.count() == 0
        page.locator('[data-edit-scrub]').fill('20')
        assert preview.count() == 1 and preview.inner_text() == 'Hello\nworld'
        assert preview.evaluate('el => getComputedStyle(el).color') == 'rgb(255, 0, 0)'
        layer = page.locator('.edit-title-preview').bounding_box(); box = preview.bounding_box()
        assert abs((box['x'] + box['width'] / 2 - layer['x']) / layer['width'] - .2) < .03
        assert abs((box['y'] + box['height'] / 2 - layer['y']) / layer['height'] - .3) < .03
        assert abs(float(preview.evaluate('el => parseFloat(getComputedStyle(el).fontSize)')) / layer['height'] - .1) < .01
        page.locator('[data-edit-scrub]').fill('50'); assert preview.count() == 0

        # Selecting the block takes the Delete key, not a clip; undo brings it back; double-click edits.
        clips = ed('edit.clips.length')
        block.click(); assert ed('frame') == 15
        page.keyboard.press('Delete'); assert titles() == [] and ed('edit.clips.length') == clips and page.locator('.edit-title').count() == 0
        page.keyboard.press('Control+z'); assert len(titles()) == 1
        page.wait_for_timeout(500)   # a double-click is two taps, not three
        page.locator('.edit-title').dblclick(); page.wait_for_selector('#tt-list [data-tt-text]')
        assert page.locator('#tt-list [data-tt-text]').input_value() == 'Hello\nworld'
        page.locator('#tt-done').click()

        page.locator('[data-edit="save"]').click()
        page.wait_for_function('!App.timelineEditor.dirty && !App.timelineEditor.saving')
        saved = store.get_episode(sid, eid)['timeline']['titles']
        assert [(x['text'], x['start_frame'], x['frames'], x['color'], x['box']) for x in saved] == [('Hello\nworld', 15, 30, '#ff0000', False)]
        page.reload(); page.locator('.edit-clip').first.wait_for()
        page.locator('[data-edit-scrub]').fill('20'); assert page.locator('.edit-title-preview .ttl').count() == 1
        assert not errors, errors
        browser.close()


def test_double_tap_on_a_clip_jumps_to_its_start(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 1100}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('[data-edit-zoom]').fill('160')
        last = page.locator('.edit-clip').last
        last.click(position={'x': 60, 'y': 40}); assert ed('frame') != 30
        page.wait_for_timeout(500)
        last.dblclick(position={'x': 60, 'y': 40})
        assert ed('frame') == 30, 'the second tap goes to the start of the clip'
        assert not errors, errors
        browser.close()


def test_pick_lock_delete_and_approve_update_in_place_without_rebuilding_the_page(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    store.update_episode(sid, eid, lambda e: e['shots'][0]['takes']['items'].append(dict(e['shots'][0]['takes']['items'][0])))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('.take-library-panel > summary').click()
        first, second = shots[0]['id'], shots[1]['id']
        page.wait_for_selector(f'[data-card="{first}"] [data-pick="1"]')
        # state that a full rebuild would destroy
        page.evaluate("""([second]) => {
            window.__editor = App.timelineEditor; window.__routes = 0;
            const original = window.route; window.route = (...a) => { window.__routes++; return original(...a); };
            document.querySelector(`[data-card="${second}"]`).__mark = 'untouched';
            App.timelineEditor.changeEdit(() => { App.timelineEditor.edit.clips[0].muted = true; });
            App.timelineEditor.seek(7);
            window.scrollTo({top: 350, behavior: 'instant'});
        }""", [second])
        y = page.evaluate('window.scrollY'); assert y > 100
        history = ed('history.length')
        chips = lambda: page.locator('#ep-head .page-head .sub').inner_text()
        press = lambda selector: page.evaluate('selector => document.querySelector(selector).click()', selector)  # no auto-scroll

        def unchanged():
            assert page.evaluate('window.__routes') == 0, 'the page must not be re-rendered'
            assert page.evaluate('App.timelineEditor === window.__editor'), 'the timeline editor must survive'
            assert ed('history.length') == history and ed('frame') == 7, 'undo history and playhead are kept'
            assert page.evaluate('document.querySelector(".take-library-panel").open') is True, 'the library stays open'
            assert abs(page.evaluate('window.scrollY') - y) < 3, 'the scroll position stays'
            assert page.evaluate(f'document.querySelector(\'[data-card="{second}"]\').__mark') == 'untouched', 'other cards keep their elements'

        # pick the second take
        press(f'[data-card="{first}"] [data-pick="1"]')
        page.wait_for_selector(f'[data-card="{first}"] .cand:nth-child(2).sel')
        assert page.locator(f'[data-card="{first}"] [data-pick="0"]').count() == 1, 'the previously picked take can now be picked'
        unchanged()
        # lock
        press(f'[data-card="{first}"] [data-lock="1"]')
        page.wait_for_selector(f'[data-card="{first}"].locked'); unchanged()
        assert page.locator(f'[data-card="{first}"] [data-lock="0"]').inner_text() == 'Unlock'
        # delete a take (with the confirmation dialog)
        press(f'[data-card="{first}"] [data-rm="0"]'); page.locator('#ask-yes').click()
        page.wait_for_function(f'document.querySelectorAll(\'[data-card="{first}"] .cand\').length === 1'); unchanged()
        assert store.get_episode(sid, eid)['shots'][0]['takes']['items'].__len__() == 1
        # approving the takes swaps the button and the stage count without a rebuild
        before = chips()
        press('[data-gate="takes"]')
        page.wait_for_selector('[data-gate="takes"][data-val="0"]'); unchanged()
        assert page.locator('[data-gate="takes"]').inner_text() == 'Reopen takes'
        assert page.locator('#ep-head .chip', has_text='stages done').inner_text() != [c for c in before.split('\n') if 'stages' in c][0]
        press('[data-gate="takes"]'); page.wait_for_selector('[data-gate="takes"][data-val="1"]'); unchanged()
        assert not errors, errors
        browser.close()


def test_frames_tab_picks_update_the_status_line_and_button_in_place(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    def candidates(ep):
        a, b = ep['shots'][0], ep['shots'][1]
        a['frame']['candidates'] = [{'file': 'a0.png', 'source': 'upload'}, {'file': 'a1.png', 'source': 'upload'}]; a['frame']['selected'] = None
        b['frame']['candidates'] = [{'file': 'b0.png', 'source': 'upload'}]; b['frame']['selected'] = 0
    store.update_episode(sid, eid, candidates)
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block').new_page()
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/status', lambda r: r.fulfill(json={'comfy': False, 'comfy_host': 'local', 'ollama_models': [], 'loras': [], 'missing': []}))
        page.goto(f'{base}/#/s/{sid}/e/{eid}/frames'); page.wait_for_selector(f'[data-card="{shots[0]["id"]}"]')
        assert 'without a frame' in page.locator('.status-line').inner_text() and page.locator('#render-missing').is_enabled()
        page.evaluate("""([second]) => { window.__routes = 0; const o = window.route; window.route = (...a) => { window.__routes++; return o(...a); };
            document.querySelector(`[data-card="${second}"]`).__mark = 'untouched'; window.scrollTo({top: 200, behavior: 'instant'}); }""", [shots[1]['id']])
        y = page.evaluate('window.scrollY')
        page.evaluate("selector => document.querySelector(selector).click()", f'[data-card="{shots[0]["id"]}"] [data-pick="1"]')
        page.wait_for_function("document.querySelector('.status-line').textContent.includes('Every shot has a frame')")
        assert page.locator('#render-missing').is_disabled() and 'primary' not in page.locator('#render-missing').get_attribute('class')
        assert page.evaluate('window.__routes') == 0 and abs(page.evaluate('window.scrollY') - y) < 3
        assert page.evaluate(f'document.querySelector(\'[data-card="{shots[1]["id"]}"]\').__mark') == 'untouched'
        assert page.locator(f'[data-card="{shots[0]["id"]}"] .cand:nth-child(2).sel').count() == 1
        # removing it again brings the warning back, still without a rebuild
        page.evaluate("selector => document.querySelector(selector).click()", f'[data-card="{shots[0]["id"]}"] [data-rm="1"]')
        page.wait_for_function("document.querySelector('.status-line').textContent.includes('without a frame')")
        assert page.locator('#render-missing').is_enabled() and page.evaluate('window.__routes') == 0
        assert not errors, errors
        browser.close()


def test_a_page_that_must_rebuild_keeps_its_scroll_position_and_open_panels(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('.take-library-panel > summary').click()
        page.evaluate("window.scrollTo({top: 420, behavior: 'instant'})")
        y = page.evaluate('window.scrollY'); assert y > 150
        page.evaluate('route()'); page.wait_for_function('App.timelineEditor && App.timelineEditor.edit.clips.length > 0')
        page.wait_for_timeout(300)
        assert page.evaluate('document.querySelector(".take-library-panel").open') is True
        assert abs(page.evaluate('window.scrollY') - y) < 3, 'a rebuild must not send you back to the top'
        assert not errors, errors
        browser.close()


def test_all_shots_checkbox_ticks_every_shot_and_drives_the_batch_render(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('.take-library-panel > summary').click()
        a, b = shots[0]['id'], shots[1]['id']
        every, button = page.locator('#chk-all'), page.locator('#render-checked')
        ticked = lambda: page.evaluate('[...document.querySelectorAll("input[data-chk]:checked")].map(x => x.dataset.chk)')
        assert button.inner_text() == 'Render checked shots' and not every.is_checked()
        every.check(); assert ticked() == [a, b] and button.inner_text() == 'Render checked shots (2)'
        # unticking one makes "All shots" partial, ticking it again completes it
        page.locator(f'input[data-chk="{b}"]').uncheck()
        assert ticked() == [a] and button.inner_text() == 'Render checked shots (1)'
        assert every.evaluate('el => el.indeterminate') and not every.is_checked()
        page.locator(f'input[data-chk="{b}"]').check(); assert every.is_checked() and not every.evaluate('el => el.indeterminate')
        # ticks survive an in-place update of a card (locking a shot repaints it)
        page.evaluate("selector => document.querySelector(selector).click()", f'[data-card="{a}"] [data-lock="1"]')
        page.wait_for_selector(f'[data-card="{a}"].locked')
        assert ticked() == [a, b] and every.is_checked() and button.inner_text() == 'Render checked shots (2)'
        # the batch asks for exactly the ticked shots, with the chosen model
        page.locator('#take-model').select_option(index=1)
        button.click(); page.wait_for_function('document.querySelector("#toasts").textContent.includes("Queued")')
        assert submitted[-1]['kind'] == 'takes' and submitted[-1]['shot_ids'] == [a, b] and submitted[-1]['force'] is True
        # ticking boxes is not an unsaved edit
        assert page.evaluate('App.dirty') is False
        every.uncheck(); assert ticked() == [] and button.inner_text() == 'Render checked shots'
        page.locator('#render-checked').click()
        playwright.expect(page.locator('#toasts')).to_contain_text('Tick some shots first')
        assert not errors, errors
        browser.close()


def test_reload_button_reloads_asks_before_discarding_changes_and_is_distinct_from_activity(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        assert page.locator('#refresh-btn use').get_attribute('href') == '#i-refresh'
        assert page.locator('#jobs-btn use').get_attribute('href') == '#i-activity', 'Activity must not look like a refresh button'
        # a plain press reloads the whole app
        page.evaluate('window.__marker = 1')
        with page.expect_navigation(timeout=15000):
            page.locator('#refresh-btn').click()
        page.locator('.edit-clip').first.wait_for()
        assert page.evaluate('window.__marker') is None, 'the page really reloaded'
        # unsaved changes: the first answer cancels, the second confirms
        page.evaluate('window.__marker = 2; App.dirty = true')
        page.locator('#refresh-btn').click()
        page.locator('#ask-no').click()
        page.wait_for_timeout(300)
        assert page.evaluate('window.__marker') == 2 and page.evaluate('App.dirty') is True, 'cancelled: nothing is lost'
        with page.expect_navigation(timeout=15000):
            page.locator('#refresh-btn').click(); page.locator('#ask-yes').click()
        page.locator('.edit-clip').first.wait_for()
        assert page.evaluate('window.__marker') is None
        # a pending timeline edit is saved first, not lost and not asked about
        page.wait_for_function('!App.timelineEditor.edit.music || App.timelineEditor.waveform')
        page.evaluate('App.timelineEditor.changeEdit(() => { App.timelineEditor.edit.clips[0].muted = true; })')
        with page.expect_navigation(timeout=15000):
            page.locator('#refresh-btn').click()
        assert store.get_episode(sid, eid)['timeline']['clips'][0]['muted'] is True
        # the command palette offers it too
        page.locator('.edit-clip').first.wait_for(); page.keyboard.press('Control+k')
        page.locator('#pal-input').fill('reload'); playwright.expect(page.locator('#pal-list')).to_contain_text('Reload the app')
        assert not errors, errors
        browser.close()


def test_an_installed_app_shows_reload_in_the_bottom_bar_instead_of_install(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, service_workers='block')
        browsed = context.new_page(); browsed.goto(f'{base}/#/')
        assert browsed.locator('#install-app').is_visible() and browsed.locator('#mobile-refresh').is_hidden(), 'in a normal browser tab'
        installed = context.new_page()
        installed.add_init_script("Object.defineProperty(navigator, 'standalone', {get: () => true})")
        installed.goto(f'{base}/#/')
        installed.locator('.mobile-nav').wait_for()
        assert installed.evaluate('document.documentElement.classList.contains("standalone")')
        assert installed.locator('#mobile-refresh').is_visible() and installed.locator('#install-app').is_hidden()
        installed.evaluate('window.__marker = 1')
        with installed.expect_navigation(timeout=15000):
            installed.locator('#mobile-refresh').tap()
        assert installed.evaluate('window.__marker') is None
        assert installed.locator('#refresh-btn').is_visible(), 'the top bar button is there on phones too'
        browser.close()


def test_delete_checked_and_delete_all_takes_confirm_skip_locked_and_update_in_place(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    a, b = shots[0]['id'], shots[1]['id']
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('.take-library-panel > summary').click()
        page.evaluate('() => { window.__routes = 0; const o = window.route; window.route = (...x) => { window.__routes++; return o(...x); }; }')
        takes = lambda: [len(s['takes']['items']) for s in store.get_episode(sid, eid)['shots']]
        clips_before = ed('edit.clips.length')
        delete_checked, delete_all = page.locator('#delete-checked'), page.locator('#delete-all')
        assert delete_checked.is_disabled() and takes() == [1, 1]

        # ticked shots only, after a confirmation that says what happens; cancelling changes nothing
        page.locator(f'input[data-chk="{a}"]').check()
        assert delete_checked.is_enabled() and delete_checked.inner_text() == 'Delete checked (1)'
        delete_checked.click()
        text = page.locator('#modal-body').inner_text()
        assert 'Delete 1 take from 1 shot' in text and 'files stay on disk' in text and 'cleared' in text
        page.locator('#ask-no').click(); assert takes() == [1, 1]
        delete_checked.click(); page.locator('#ask-yes').click()
        page.wait_for_function(f'document.querySelectorAll(\'[data-card="{a}"] .cand\').length === 0')
        assert takes() == [0, 1] and store.get_episode(sid, eid)['outputs'] == {}
        assert store.get_episode(sid, eid)['gates']['takes'] is False
        assert page.evaluate('window.__routes') == 0, 'updated in place'
        assert ed('edit.clips.length') == clips_before, 'the saved timeline keeps its clips'
        assert delete_checked.is_disabled() and not page.locator('#chk-all').is_checked(), 'ticks are cleared'
        import pathlib
        assert all(pathlib.Path(store.abs_media(t['file'])).exists() for s in store.get_episode(sid, eid)['shots'] for t in s['takes']['items']), 'files stay'

        # a locked shot is skipped; with nothing else left there is nothing to delete
        page.evaluate("selector => document.querySelector(selector).click()", f'[data-card="{b}"] [data-lock="1"]')
        page.wait_for_selector(f'[data-card="{b}"].locked')
        delete_all.click()
        playwright.expect(page.locator('#toasts')).to_contain_text('locked')
        assert takes() == [0, 1] and page.locator('#modal').is_hidden()
        page.evaluate("selector => document.querySelector(selector).click()", f'[data-card="{b}"] [data-lock="0"]')
        page.wait_for_function(f'!document.querySelector(\'[data-card="{b}"]\').classList.contains("locked")')
        # delete everything
        delete_all.click(); assert 'Delete 1 take from 1 shot' in page.locator('#modal-body').inner_text()
        page.locator('#ask-yes').click()
        page.wait_for_function('document.querySelectorAll("#take-library .cand").length === 0')
        assert takes() == [0, 0] and page.evaluate('window.__routes') == 0
        delete_all.click(); playwright.expect(page.locator('#toasts')).to_contain_text('no takes to delete')
        assert not errors, errors
        browser.close()


def test_the_video_model_menu_does_not_offer_an_engine_that_is_not_installed(edit_server):
    base, sid, eid, shots, music, submitted = edit_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block').new_page()
        errors, ed = _open_editor(page, base, sid, eid)
        page.locator('.take-library-panel > summary').click()
        ltx, wan = page.locator('#take-model option[value="ltx"]'), page.locator('#take-model option[value="wan"]')
        assert ltx.evaluate('el => el.disabled') is True and 'not installed' in ltx.inner_text() and 'workflow file is missing' in ltx.get_attribute('title')
        assert wan.evaluate('el => el.disabled') is False and 'not installed' not in wan.inner_text()
        page.locator('#take-model').select_option('wan')
        assert page.evaluate('document.querySelector("#take-model").selectedOptions[0].dataset.engine') == 'wan'
        assert not errors, errors
        browser.close()
