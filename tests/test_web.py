import re

from PIL import Image
from fastapi.testclient import TestClient

from api import app
from core.cover import cover_hook, stamp_cover


def _page_source(client: TestClient) -> str:
    page = client.get("/")
    assert page.status_code == 200
    text = page.text
    for url in re.findall(r'(?:src|href)="(/assets/[^"]+)"', text):
        asset = client.get(url)
        assert asset.status_code == 200, url
        text += asset.text
    return text


def test_web_page_and_task_list():
    client = TestClient(app)
    page = _page_source(client)
    assert "生成分镜" in page
    assert "确认并开始制作" in page
    assert "停止生成" in page
    assert "退回分镜重新生成" in page
    config = client.get("/config")
    assert config.status_code == 200
    assert "llm_ready" in config.json()
    assert "unsplash_ready" in config.json()
    assert "freesound_ready" in config.json()
    assert "Freesound API Key" in page
    assert "自动成片" in page
    assert "密钥设置" in page
    assert "必填" in page
    assert "选填" in page
    assert "字幕大小" in page
    assert "手动指定字号" in page
    assert "导入剪映" in page
    listed = client.get("/tasks")
    assert listed.status_code == 200
    assert isinstance(listed.json()["tasks"], list)
    assert client.get("/tasks/not-a-task").status_code == 404


def test_draft_waits_for_confirmation():
    import api as api_module

    client = TestClient(app)
    task_id = ""
    try:
        created = client.post(
            "/tasks/draft",
            json={"topic": "为什么天空是蓝色的", "duration": 8, "aspect_ratio": "9:16", "use_llm": False, "quality": "draft"},
        )
        assert created.status_code == 200
        task_id = created.json()["task_id"]
        status = client.get(f"/tasks/{task_id}").json()
        assert status["state"] == "review"
        assert status["storyboard"]["scenes"]
        preview = client.post("/voices/preview", json={"voice": "not-a-voice", "text": "你好"})
        assert preview.status_code == 400
        assert client.post(f"/tasks/{task_id}/stop").status_code == 409
        reopened = client.post(f"/tasks/{task_id}/reopen")
        assert reopened.status_code == 200
        assert client.get(f"/tasks/{task_id}").json()["state"] == "review"
        from core.workspace import load_task

        load_task(task_id).status(state="running", step="voice")
        api_module._busy = True
        api_module._active_id = task_id
        stopped = client.post(f"/tasks/{task_id}/stop")
        assert stopped.status_code == 200
        assert client.get(f"/tasks/{task_id}").json()["state"] == "stopping"
        api_module._busy = False
        api_module._active_id = None
        settled = client.post(f"/tasks/{task_id}/stop")
        assert settled.status_code == 200
        assert client.get(f"/tasks/{task_id}").json()["state"] == "review"
    finally:
        api_module._busy = False
        api_module._active_id = None
        if task_id:
            client.delete(f"/tasks/{task_id}")


def test_delete_task_removes_the_folder_and_keeps_a_running_one():
    import api as api_module

    client = TestClient(app)
    created = client.post(
        "/tasks/draft",
        json={"topic": "为什么天空是蓝色的", "duration": 8, "aspect_ratio": "9:16", "use_llm": False, "quality": "draft"},
    )
    task_id = created.json()["task_id"]
    from core.workspace import load_task

    try:
        load_task(task_id).status(state="running", step="voice")
        api_module._busy = True
        api_module._active_id = task_id
        refused = client.delete(f"/tasks/{task_id}")
        assert refused.status_code == 409
        assert client.get(f"/tasks/{task_id}").status_code == 200
    finally:
        api_module._busy = False
        api_module._active_id = None
    deleted = client.delete(f"/tasks/{task_id}")
    assert deleted.status_code == 200
    assert client.get(f"/tasks/{task_id}").status_code == 404
    assert client.delete(f"/tasks/{task_id}").status_code == 404


def test_cover_stamp_uses_a_short_hook():
    assert cover_hook("为什么天空是蓝色的") == ["天空是蓝"]
    assert cover_hook("什么是总观效应") == ["总观效应"]
    image = Image.new("RGB", (432, 768), (20, 30, 40))
    stamped = stamp_cover(image, "为什么天空是蓝色的")
    assert stamped.size == (432, 768)
    assert stamped.tobytes() != image.tobytes()
