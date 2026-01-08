"""运行时启动与性能相关的初始化。

关键点：
- 必须在导入 onnxruntime / rapidocr_onnxruntime 之前设置线程环境变量，才更可能生效。
- OpenCV 的线程数也尽量尽早关闭，避免与 ORT 竞争 CPU。
"""

from __future__ import annotations

import os
from typing import Any, Optional


def bootstrap_runtime(
    *,
    omp_num_threads: str = "2",
    mkl_num_threads: str = "2",
    openblas_num_threads: str = "2",
    numexpr_num_threads: str = "2",
    ort_logger_severity: int = 3,
) -> Optional[Any]:
    """设置线程环境变量、配置 onnxruntime 日志、并关闭 OpenCV 多线程。

    返回：
    - onnxruntime 模块（若可用）
    - None（若导入失败）
    """

    # 尽早限制推理线程数：必须放在 onnxruntime / rapidocr 导入之前才更有效
    # 说明：用户仍可通过环境变量覆盖这些默认值
    os.environ.setdefault("OMP_NUM_THREADS", str(omp_num_threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(mkl_num_threads))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(openblas_num_threads))
    os.environ.setdefault("NUMEXPR_NUM_THREADS", str(numexpr_num_threads))

    ort_mod: Optional[Any]
    try:
        import onnxruntime as ort  # RapidOCR 底层依赖

        # 关闭冗余日志，减少开销
        ort.set_default_logger_severity(int(ort_logger_severity))
        ort_mod = ort
    except Exception:
        ort_mod = None

    # 强制禁用 opencv 的多线程，防止冲突
    try:
        import cv2

        cv2.setNumThreads(0)
    except Exception:
        pass

    return ort_mod

