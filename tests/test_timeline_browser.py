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
