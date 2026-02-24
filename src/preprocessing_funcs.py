import os
import shutil
import re
import pandas as pd
from pathlib import Path
from deeplabcut.utils import auxiliaryfunctions

def organize_for_anipose(parent_path, folder_list, scorer='User'):
    """
    Groups cam0/cam1 folders into a unified 2d-data structure for Anipose.
    """
    # 1. Define the base 2d-data directory
    anipose_2d_path = os.path.join(parent_path, 'pose-2d')
    if not os.path.isdir(anipose_2d_path):
        os.makedirs(anipose_2d_path)
        print(f"Created: {anipose_2d_path}")

    for folder_name in folder_list:
        # Source path (where the files currently live)
        src_folder = os.path.join(parent_path, folder_name)
        
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



def convert_mediapipe_csv_to_h5(parent_path, folder_list, scorer='User'):
    """
    Refined conversion that includes scorer overwriting and index verification.
    """
    for folder_name in folder_list:
        # Navigate to the specific labeled-data subfolder
        folder_path = os.path.join(parent_path, folder_name)
        
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
