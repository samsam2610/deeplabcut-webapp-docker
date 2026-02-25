import os
import shutil
import re
import numpy as np
import pandas as pd
from pathlib import Path
from deeplabcut.utils import auxiliaryfunctions

def organize_for_anipose(config, parent_path, folder_list, scorer='User'):
    """
    Groups cam0/cam1 folders into a unified 2d-data structure for Anipose.
    """
    # Get list of folders in the pipeline_mediapipe_2d directory
    pipeline_mediapipe_2d = config["pipeline"]["mediapipe_processed"]
    mediapipe_folder_path = os.path.join(parent_path, pipeline_mediapipe_2d)
    mediapipe_folder_list = [f for f in os.listdir(mediapipe_folder_path) if os.path.isdir(os.path.join(mediapipe_folder_path, f))]

    # Define the base 2d-data directory
    pipeline_pose_2d  = config["pipeline"]["pose_2d"]
    anipose_2d_path = os.path.join(parent_path, pipeline_pose_2d)
    if not os.path.isdir(anipose_2d_path):
        os.makedirs(anipose_2d_path)
        print(f"Created: {anipose_2d_path}")

    for folder_name in mediapipe_folder_list:
        # Source path (where the files currently live)
        src_folder = os.path.join(mediapipe_folder_path, folder_name)
        
        # 2. Extract the Trial ID (Everything except the 'camX' part)
        # This groups 'sam_backpack_cam0_...' and 'sam_backpack_cam1_...' into one folder
        trial_id = re.sub(r'_cam[0-9]_', '_', folder_name)
        
        # Create the destination trial folder inside 2d-data
        dest_trial_path = os.path.join(anipose_2d_path, trial_id)
        if not os.path.isdir(dest_trial_path):
            os.makedirs(dest_trial_path)

        # 3. Identify files to move
        files_to_move = [
            f"CollectedData_{scorer}.csv",
            f"CollectedData_{scorer}.h5"
        ]

        for file_name in files_to_move:
            src_file = os.path.join(src_folder, file_name)
            
            if os.path.exists(src_file):
                # We rename the file to include the camera name so Anipose can distinguish them
                # Example: cam0_CollectedData_User.h5
                cam_match = re.search(r'cam[0-9]', folder_name)
                cam_name = cam_match.group(0) if cam_match else "unknown"
                
                new_file_name = f"{cam_name}_{file_name}"
                dest_file = os.path.join(dest_trial_path, new_file_name)

                # Move the file
                shutil.copy2(src_file, dest_file) # Use copy2 to preserve metadata, or shutil.move
                print(f"Moved: {folder_name}/{file_name} -> pose-2d/{trial_id}/{new_file_name}")
            else:
                print(f"Warning: {src_file} not found.")



def convert_mediapipe_csv_to_h5(config, parent_path, folder_list, scorer='User'):
    """
    Refined conversion that includes scorer overwriting and index verification.
    """
    # Get list of folders in the pipeline_mediapipe_2d directory
    pipeline_mediapipe_2d = config["pipeline"]["mediapipe_processed"]
    mediapipe_folder_path = os.path.join(parent_path, pipeline_mediapipe_2d)
    mediapipe_folder_list = [f for f in os.listdir(mediapipe_folder_path) if os.path.isdir(os.path.join(mediapipe_folder_path, f))]
    for folder_name in mediapipe_folder_list:
        # Navigate to the specific labeled-data subfolder
        folder_path = os.path.join(mediapipe_folder_path, folder_name)
        
        # Note: Scorer here must match the filename created by MATLAB
        csv_filename = f"CollectedData_{scorer}.csv"
        csv_path = os.path.join(folder_path, csv_filename)
        h5_path = csv_path.replace(".csv", ".h5")

        if not os.path.exists(csv_path):
            print(f"Attention: {folder_name} does not have labeled data!")
            continue

        try:
            print(f"Processing {folder_name}...")
            
            # 1. Load the CSV with the 3-tier header
            # index_col=0 is the column containing '0.png', '1.png', etc.
            data = pd.read_csv(csv_path, header=[0, 1, 2], index_col=0)

            # 2. Update/Overwrite the scorer level
            # This ensures that even if MATLAB wrote 'User', 
            # you can change it to 'Sam' or whatever is in your config.
            data.columns = data.columns.set_levels([scorer], level="scorer")

            # 3. Ensure row indexing matches DLC expectations
            # This handles internal formatting of the image paths 
            # auxiliaryfunctions.guarantee_multiindex_rows(data)

            # 4. Save to HDF5 with the mandatory key
            data.to_hdf(h5_path, key="df_with_missing", mode="w")
            
            # Optional: Overwrite the CSV to ensure it's in sync with the H5
            data.to_csv(csv_path)
            
            print(f"Successfully converted to H5.")

        except Exception as e:
            print(f"Error processing {folder_name}: {e}")


def convert_mediapipe_to_dlc_csv(config, parent_path, frame_w, frame_h, scorer='User'):
    """
    Convert raw MediaPipe .mat arrays to DLC-format labeled-data CSVs.

    Scans each subfolder of pipeline['mediapipe_processed'] for a .mat file
    containing a 3-D array of shape (numFrames, numLandmarks, >=4) where
    axis-2 channels are [x_norm, y_norm, z_norm, visibility].
    MediaPipe coordinates are normalized (0–1); they are scaled to pixels
    using frame_w and frame_h.

    Writes  CollectedData_{scorer}.csv  into each subfolder, matching the
    multi-index header format expected by DeepLabCut / Anipose.
    """
    import scipy.io

    pipeline_mediapipe_2d = config["pipeline"]["mediapipe_processed"]
    mediapipe_folder_path = os.path.join(parent_path, pipeline_mediapipe_2d)

    if not os.path.isdir(mediapipe_folder_path):
        print(f"Warning: mediapipe folder not found: {mediapipe_folder_path}")
        return

    subfolders = sorted([
        f for f in os.listdir(mediapipe_folder_path)
        if os.path.isdir(os.path.join(mediapipe_folder_path, f))
    ])

    print(f"Found {len(subfolders)} subfolder(s): {subfolders}")
    print(f"Frame size : {frame_w} x {frame_h}  |  Scorer: {scorer}")

    for folder_name in subfolders:
        folder_path = os.path.join(mediapipe_folder_path, folder_name)
        mat_files   = sorted([f for f in os.listdir(folder_path) if f.endswith('.mat')])

        if not mat_files:
            print(f"  [{folder_name}] No .mat file — skipping.")
            continue

        mat_path = os.path.join(folder_path, mat_files[0])
        print(f"  [{folder_name}] Loading {mat_files[0]} …")

        try:
            mat_data  = scipy.io.loadmat(mat_path)
            data_keys = [k for k in mat_data if not k.startswith('_')]
            if not data_keys:
                print(f"  [{folder_name}] No data variables in .mat — skipping.")
                continue

            mp_array = mat_data[data_keys[0]]

            # Expected shape: (numFrames, numLandmarks, >=4)
            # axis-2: [x_norm, y_norm, z_norm, visibility]
            if mp_array.ndim != 3 or mp_array.shape[2] < 4:
                print(f"  [{folder_name}] Unexpected shape {mp_array.shape} "
                      f"(need (frames, landmarks, >=4)) — skipping.")
                continue

            num_frames, num_landmarks, _ = mp_array.shape
            print(f"  [{folder_name}] {num_frames} frames, {num_landmarks} landmarks")

            # Build DLC data matrix: [lm0_x, lm0_y, lm0_like, lm1_x, ...]
            dlc_data = np.zeros((num_frames, num_landmarks * 3))
            for i in range(num_landmarks):
                dlc_data[:, i*3 + 0] = mp_array[:, i, 0] * frame_w  # x → pixels
                dlc_data[:, i*3 + 1] = mp_array[:, i, 1] * frame_h  # y → pixels
                dlc_data[:, i*3 + 2] = mp_array[:, i, 3]            # visibility

            bodyparts = [str(i) for i in range(num_landmarks)]

            # Three-row multi-index header
            h1 = ['scorer']    + [scorer] * (num_landmarks * 3)
            h2 = ['bodyparts'] + [bp for bp in bodyparts for _ in range(3)]
            h3 = ['coords']    + ['x', 'y', 'likelihood'] * num_landmarks

            image_names = [str(i) for i in range(num_frames)]
            data_rows   = [[image_names[i]] + list(dlc_data[i]) for i in range(num_frames)]

            output_csv = os.path.join(folder_path, f"CollectedData_{scorer}.csv")
            pd.DataFrame([h1, h2, h3] + data_rows).to_csv(output_csv, index=False, header=False)
            print(f"  [{folder_name}] Saved {num_frames} frames → {output_csv}")

        except Exception as e:
            print(f"  [{folder_name}] Error: {e}")
