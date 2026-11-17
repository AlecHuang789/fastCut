"""Command line entry for the automatic editing pipeline."""

from __future__ import annotations

import argparse
import sys

from core.cleanup import cleanup_expired
from core.pipeline import PipelineError, run_create, run_highlight


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fastcut",
        description="全自动 AI 视频生成与剪辑。输入主题或长视频，输出 mp4。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("run", help="从一句话主题生成成片")
    create.add_argument("--topic", required=True, help="视频主题")
    create.add_argument("--duration", type=float, default=30, help="目标秒数，默认 30")
    create.add_argument("--aspect", choices=["9:16", "16:9", "4:3"], default="9:16")
    create.add_argument("--voice", default=None, help="edge-tts 声音名，默认 zh-CN-XiaoxiaoNeural")
    create.add_argument("--bgm", default=None, help="背景音乐文件")
    create.add_argument("--quality", choices=["draft", "looks", "delivery"], default="draft")
    create.add_argument("--align", choices=["auto", "estimate", "faster-whisper", "hyperframes"], default="auto")
    create.add_argument("--no-llm", action="store_true", help="不调用大模型，使用本地导演")
    create.add_argument("--task-id", default=None)

    highlight = sub.add_parser("highlight", help="从长视频里切高光")
    highlight.add_argument("--video", required=True, help="本地文件或 http(s) 地址")
    highlight.add_argument("--duration", type=float, default=30)
    highlight.add_argument("--aspect", choices=["9:16", "16:9", "4:3"], default="9:16")
    highlight.add_argument("--bgm", default=None)
    highlight.add_argument("--quality", choices=["draft", "looks", "delivery"], default="draft")
    highlight.add_argument("--align", choices=["auto", "estimate", "faster-whisper", "hyperframes"], default="auto")
    highlight.add_argument("--no-llm", action="store_true")
    highlight.add_argument("--task-id", default=None)

    cleanup = sub.add_parser("cleanup", help="删除超过保留时间的任务目录和素材缓存")
    cleanup.add_argument("--hours", type=float, default=24)

    serve = sub.add_parser("serve", help="启动网页和接口")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--open", action="store_true", help="启动后打开浏览器")

    web = sub.add_parser("web", help="打开网页界面")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    return parser


def _check_duration(duration: float) -> float:
    if duration < 5 or duration > 180:
        raise SystemExit("时长要在 5 到 180 秒之间")
    return duration


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "run":
            result = run_create(
                args.topic,
                _check_duration(args.duration),
                args.aspect,
                voice=args.voice,
                bgm=args.bgm,
                quality=args.quality,
                use_llm=not args.no_llm,
                align=args.align,
                task_id=args.task_id,
            )
            if result is None:
                print("已停止")
                return 0
            _print_result(result)
            return 0
        if args.command == "highlight":
            result = run_highlight(
                args.video,
                _check_duration(args.duration),
                args.aspect,
                bgm=args.bgm,
                quality=args.quality,
                use_llm=not args.no_llm,
                align=args.align,
                task_id=args.task_id,
            )
            if result is None:
                print("已停止")
                return 0
            _print_result(result)
            return 0
        if args.command == "cleanup":
            removed = cleanup_expired(args.hours)
            print(f"已清理 {len(removed)} 项")
            return 0
        if args.command in {"serve", "web"}:
            _serve(args.host, args.port, open_browser=args.command == "web" or getattr(args, "open", False))
            return 0
    except (PipelineError, FileExistsError, FileNotFoundError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    return 2


def _serve(host: str, port: int, open_browser: bool) -> None:
    import threading
    import webbrowser

    import uvicorn

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run("api:app", host=host, port=port, reload=False)


def _print_result(result) -> None:
    print(f"成片：{result.output_path}")
    print(f"时长：{result.duration:.2f}s")
    if result.notes:
        print("备注：")
        for note in result.notes:
            print(f"- {note}")


if __name__ == "__main__":
    raise SystemExit(main())
