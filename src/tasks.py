"""
Celery worker entry point.
Run with: celery -A tasks worker --loglevel=info

All task implementations live in anipose_tasks.py and dlc_tasks.py.
This module imports them so Celery can discover them by their registered names.
"""
from celery_app import celery  # noqa: F401 — re-export for `celery -A tasks`

from anipose_tasks import (  # noqa: F401
    process_calibrate,
    process_filter_2d,
    process_triangulate,
    process_filter_3d,
    process_organize_for_anipose,
    process_convert_mediapipe_csv_to_h5,
    process_convert_3d_csv_to_mat,
    process_convert_mediapipe_to_dlc_csv,
    init_anipose_session,
)

from dlc_tasks import (  # noqa: F401
    dlc_create_training_dataset,
    dlc_add_datasets_to_video_list,
    dlc_convert_labels_to_h5,
    dlc_train_network,
    dlc_probe_gpu_stats,
    dlc_analyze,
    dlc_machine_label_frames,
    dlc_machine_label_reapply,
    run_processing,
)
