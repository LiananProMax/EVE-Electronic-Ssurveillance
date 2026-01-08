from __future__ import annotations

from typing import Any, Callable, Optional

from ..constants import DEFAULT_ORT_INTER_THREADS, DEFAULT_ORT_INTRA_THREADS


def create_rapidocr_engine(
    *,
    ort: Optional[Any],
    log: Callable[[str], None],
    intra_threads: int = DEFAULT_ORT_INTRA_THREADS,
    inter_threads: int = DEFAULT_ORT_INTER_THREADS,
) -> Any:
    """创建 RapidOCR 引擎（CPU）。

    说明：
    - RapidOCR 不同版本对线程参数支持不同，这里做兼容回退。
    - 为了确保 bootstrap 的线程环境变量先设置，本函数内部才导入 RapidOCR。
    """

    log("⏳ 正在加载 RapidOCR 引擎...")

    # 延迟导入：避免在 bootstrap 前触发 onnxruntime / rapidocr 加载
    from rapidocr_onnxruntime import RapidOCR  # noqa: WPS433

    try:
        engine = RapidOCR(
            det_use_cuda=False,
            cls_use_cuda=False,
            rec_use_cuda=False,
            # RapidOCR >= 1.3 支持
            intra_op_num_threads=int(intra_threads),
            inter_op_num_threads=int(inter_threads),
        )
        log(f"✅ RapidOCR 引擎已就绪（CPU，线程限制: {int(intra_threads)}）。")
        return engine
    except TypeError:
        engine = RapidOCR(
            det_use_cuda=False,
            cls_use_cuda=False,
            rec_use_cuda=False,
        )
        log("✅ RapidOCR 引擎已就绪（CPU，环境变量线程控制）。")
        return engine
    except Exception as e:
        log(f"❌ 加载失败：{e}")
        return None

