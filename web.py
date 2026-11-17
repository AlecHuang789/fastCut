"""Small Gradio console. Install gradio before launching."""

from __future__ import annotations


def launch(port: int = 7860) -> None:
    try:
        import gradio as gr
    except ImportError as exc:
        raise SystemExit("网页控制台需要先安装 gradio：pip install gradio") from exc

    from core.pipeline import run_create

    def generate(topic: str, duration: float, aspect: str):
        if not topic.strip():
            raise gr.Error("先写一个主题")
        result = run_create(topic.strip(), float(duration), aspect, use_llm=True, quality="draft", align="auto")
        notes = "\n".join(result.notes) if result.notes else "完成"
        return str(result.output_path), notes

    demo = gr.Interface(
        fn=generate,
        inputs=[
            gr.Textbox(label="主题", placeholder="为什么天空是蓝色的"),
            gr.Slider(5, 90, value=20, step=1, label="目标秒数"),
            gr.Radio(["9:16", "16:9"], value="9:16", label="画面比例"),
        ],
        outputs=[gr.Video(label="成片"), gr.Textbox(label="备注")],
        title="fastCut",
        description="输入主题，自动写分镜、配音、找画面并合成。",
    )
    demo.launch(server_port=port, server_name="127.0.0.1")
