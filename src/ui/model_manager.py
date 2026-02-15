import os
import shutil
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, 
    QPushButton, QLabel, QHeaderView, QMessageBox, QProgressBar, QFrame
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDir
from PyQt5.QtGui import QFont
import subprocess
import sys

from ui.base_window import BaseWindow
from utils import ConfigManager


class ModelDownloadThread(QThread):
    """Thread for downloading models to avoid blocking the UI."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, model_name):
        super().__init__()
        self.model_name = model_name
        
    def run(self):
        try:
            self.progress.emit(f"Downloading {self.model_name}...")
            
            # Import here to avoid issues if faster-whisper isn't available
            from faster_whisper import WhisperModel
            
            # This will trigger the download
            model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            
            self.progress.emit(f"Download completed for {self.model_name}")
            self.finished.emit(True, f"Successfully downloaded {self.model_name}")
            
        except Exception as e:
            self.finished.emit(False, f"Failed to download {self.model_name}: {str(e)}")


class ModelManagerWindow(BaseWindow):
    def __init__(self):
        super().__init__('Model Manager', 900, 600)
        self.download_threads = {}  # Keep track of download threads
        self.init_ui()
        self.refresh_model_list()
        
    def init_ui(self):
        """Initialize the user interface."""
        # Title
        title_label = QLabel("Whisper Model Manager")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(title_label)
        
        # Description
        desc_label = QLabel("Manage your local Whisper models. Download new models or remove existing ones.")
        desc_label.setAlignment(Qt.AlignCenter)
        desc_label.setStyleSheet("color: gray; margin-bottom: 10px;")
        self.main_layout.addWidget(desc_label)
        
        # Model table
        self.model_table = QTableWidget()
        self.model_table.setColumnCount(7)
        self.model_table.setHorizontalHeaderLabels([
            "Model Name", "Size", "Status", "Location", "Select", "Download", "Delete"
        ])
        
        # Set column widths
        header = self.model_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # Model name
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Size
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Status
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # Location
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Select
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Download
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # Delete
        
        self.main_layout.addWidget(self.model_table)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_model_list)
        button_layout.addWidget(refresh_btn)
        
        open_folder_btn = QPushButton("Open Models Folder")
        open_folder_btn.clicked.connect(self.open_models_folder)
        button_layout.addWidget(open_folder_btn)
        
        button_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)
        
        self.main_layout.addLayout(button_layout)
        
    def get_available_models(self):
        """Get list of available Whisper models."""
        return [
            "tiny", "tiny.en", "base", "base.en", "small", "small.en",
            "medium", "medium.en", "large", "large-v1", "large-v2", "large-v3",
            "large-v3-turbo", "distil-large-v2", "distil-large-v3"
        ]
    
    def get_model_sizes(self):
        """Get approximate sizes for models in MB."""
        return {
            "tiny": 39,
            "tiny.en": 39,
            "base": 74,
            "base.en": 74,
            "small": 244,
            "small.en": 244,
            "medium": 769,
            "medium.en": 769,
            "large": 1550,
            "large-v1": 1550,
            "large-v2": 1550,
            "large-v3": 1550,
            "large-v3-turbo": 1620,
            "distil-large-v2": 775,
            "distil-large-v3": 775
        }
    
    def get_models_directory(self):
        """Get the directory where models are stored."""
        # faster-whisper stores models in ~/.cache/huggingface/hub/
        home_dir = os.path.expanduser("~")
        cache_dir = os.path.join(home_dir, ".cache", "huggingface", "hub")
        return cache_dir
    
    def is_model_downloaded(self, model_name):
        """Check if a model is downloaded."""
        cache_dir = self.get_models_directory()
        
        # Map model names to their folder patterns
        model_folder_patterns = {
            "tiny": "models--Systran--faster-whisper-tiny",
            "tiny.en": "models--Systran--faster-whisper-tiny.en",
            "base": "models--Systran--faster-whisper-base",
            "base.en": "models--Systran--faster-whisper-base.en",
            "small": "models--Systran--faster-whisper-small",
            "small.en": "models--Systran--faster-whisper-small.en",
            "medium": "models--Systran--faster-whisper-medium",
            "medium.en": "models--Systran--faster-whisper-medium.en",
            "large": "models--Systran--faster-whisper-large",
            "large-v1": "models--Systran--faster-whisper-large-v1",
            "large-v2": "models--Systran--faster-whisper-large-v2",
            "large-v3": "models--Systran--faster-whisper-large-v3",
            "large-v3-turbo": "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
            "distil-large-v2": "models--Systran--faster-distil-whisper-large-v2",
            "distil-large-v3": "models--Systran--faster-distil-whisper-large-v3"
        }
        
        folder_pattern = model_folder_patterns.get(model_name)
        if not folder_pattern:
            return False, None
        
        # Look for the specific folder pattern
        for item in os.listdir(cache_dir):
            item_path = os.path.join(cache_dir, item)
            if os.path.isdir(item_path) and folder_pattern in item:
                # Check if the folder contains model files
                for root, dirs, files in os.walk(item_path):
                    for file in files:
                        if file.endswith('.bin') or file.endswith('.safetensors'):
                            return True, item_path
                break
        
        return False, None
    
    def get_model_folder_size(self, model_path):
        """Get the size of a model folder in MB."""
        if not model_path or not os.path.exists(model_path):
            return 0
        
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(model_path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if os.path.exists(filepath):
                    total_size += os.path.getsize(filepath)
        
        return round(total_size / (1024 * 1024), 1)  # Convert to MB
    
    def refresh_model_list(self):
        """Refresh the model list table."""
        self.model_table.setRowCount(0)
        models = self.get_available_models()
        sizes = self.get_model_sizes()
        
        # Get currently selected model
        current_model = ConfigManager.get_config_value('model_options', 'local', 'model')
        
        for i, model_name in enumerate(models):
            self.model_table.insertRow(i)
            
            # Model name
            name_item = QTableWidgetItem(model_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.model_table.setItem(i, 0, name_item)
            
            # Size
            size_mb = sizes.get(model_name, "Unknown")
            size_item = QTableWidgetItem(f"{size_mb} MB")
            size_item.setFlags(size_item.flags() & ~Qt.ItemIsEditable)
            self.model_table.setItem(i, 1, size_item)
            
            # Status
            is_downloaded, model_path = self.is_model_downloaded(model_name)
            if is_downloaded:
                status_item = QTableWidgetItem("Downloaded")
                status_item.setBackground(Qt.green)
            else:
                status_item = QTableWidgetItem("Not Downloaded")
                status_item.setBackground(Qt.lightGray)
            status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
            self.model_table.setItem(i, 2, status_item)
            
            # Location
            if model_path:
                location_item = QTableWidgetItem(os.path.dirname(model_path))
            else:
                location_item = QTableWidgetItem("N/A")
            location_item.setFlags(location_item.flags() & ~Qt.ItemIsEditable)
            self.model_table.setItem(i, 3, location_item)
            
            # Select button
            if is_downloaded:
                if model_name == current_model:
                    select_btn = QPushButton("✓ Selected")
                    select_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
                else:
                    select_btn = QPushButton("Select")
                    select_btn.setStyleSheet("background-color: #2196F3; color: white;")
            else:
                select_btn = QPushButton("Select")
                select_btn.setEnabled(False)
                select_btn.setStyleSheet("background-color: #cccccc; color: #666666;")
            
            select_btn.clicked.connect(lambda checked, name=model_name: self.select_model(name))
            self.model_table.setCellWidget(i, 4, select_btn)
            
            # Download button
            download_btn = QPushButton("Download" if not is_downloaded else "Re-download")
            download_btn.clicked.connect(lambda checked, name=model_name: self.download_model(name))
            self.model_table.setCellWidget(i, 5, download_btn)
            
            # Delete button
            delete_btn = QPushButton("Delete")
            delete_btn.setEnabled(is_downloaded)
            delete_btn.clicked.connect(lambda checked, name=model_name: self.delete_model(name))
            self.model_table.setCellWidget(i, 6, delete_btn)
    
    def download_model(self, model_name):
        """Download a model."""
        if model_name in self.download_threads and self.download_threads[model_name].isRunning():
            QMessageBox.information(self, "Download in Progress", 
                                  f"Download of {model_name} is already in progress.")
            return
        
        reply = QMessageBox.question(self, "Download Model", 
                                   f"Download {model_name}? This may take several minutes.",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            # Create and start download thread
            download_thread = ModelDownloadThread(model_name)
            download_thread.progress.connect(lambda msg: self.update_download_progress(model_name, msg))
            download_thread.finished.connect(lambda success, msg: self.download_finished(model_name, success, msg))
            
            self.download_threads[model_name] = download_thread
            download_thread.start()
            
            # Update button to show progress
            for i in range(self.model_table.rowCount()):
                if self.model_table.item(i, 0).text() == model_name:
                    progress_btn = QPushButton("Downloading...")
                    progress_btn.setEnabled(False)
                    self.model_table.setCellWidget(i, 5, progress_btn)
                    break
    
    def update_download_progress(self, model_name, message):
        """Update download progress message."""
        # Could be enhanced with actual progress bar
        pass
    
    def select_model(self, model_name):
        """Select a model for use."""
        ConfigManager.set_config_value(model_name, 'model_options', 'local', 'model')
        ConfigManager.save_config()
        
        QMessageBox.information(self, "Model Selected", 
                              f"{model_name} has been selected as the active model.\n\n"
                              "The change will take effect when you restart Screamscriber.")
        
        # Refresh the table to update the selection indicators
        self.refresh_model_list()

    def download_finished(self, model_name, success, message):
        """Handle download completion."""
        if success:
            QMessageBox.information(self, "Download Complete", message)
        else:
            QMessageBox.warning(self, "Download Failed", message)
        
        # Clean up thread
        if model_name in self.download_threads:
            self.download_threads[model_name].deleteLater()
            del self.download_threads[model_name]
        
        # Refresh the table
        self.refresh_model_list()
    
    def delete_model(self, model_name):
        """Delete a downloaded model."""
        is_downloaded, model_path = self.is_model_downloaded(model_name)
        
        if not is_downloaded:
            QMessageBox.warning(self, "Model Not Found", f"{model_name} is not downloaded.")
            return
        
        reply = QMessageBox.question(self, "Delete Model", 
                                   f"Delete {model_name}? This will free up disk space but you'll need to download it again to use it.",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            try:
                # Delete the model directory directly
                if model_path and os.path.exists(model_path):
                    shutil.rmtree(model_path)
                    QMessageBox.information(self, "Model Deleted", f"{model_name} has been deleted.")
                    self.refresh_model_list()
                else:
                    QMessageBox.warning(self, "Delete Failed", f"Could not find {model_name} to delete.")
                
            except Exception as e:
                QMessageBox.critical(self, "Delete Error", f"Error deleting {model_name}: {str(e)}")
    
    def open_models_folder(self):
        """Open the models folder in file manager."""
        cache_dir = self.get_models_directory()
        
        if not os.path.exists(cache_dir):
            QMessageBox.information(self, "No Models", "No models have been downloaded yet.")
            return
        
        try:
            if sys.platform == "win32":
                os.startfile(cache_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", cache_dir])
            else:
                subprocess.run(["xdg-open", cache_dir])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open folder: {str(e)}") 