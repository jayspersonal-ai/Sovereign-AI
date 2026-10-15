import os
import asyncio
import json
import time
import random
import urllib.request
import urllib.parse
import mimetypes
import glob
import shutil
import zipfile
from typing import Optional
from PIL import Image

WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://comfyui:8181")

def _workspace_root() -> str:
    return os.path.realpath(WORKSPACE_DIR)

def _is_inside_workspace(path: str) -> bool:
    """True only if the path is the workspace itself or somewhere beneath it."""
    root = _workspace_root()
    try:
        return os.path.commonpath([root, os.path.realpath(path)]) == root
    except ValueError:
        return False

def _get_safe_path(filename: str) -> str:
    """Enforces directory sandboxing to prevent path traversal."""
    safe_path = os.path.abspath(os.path.join(WORKSPACE_DIR, filename))
    # A plain startswith() check would also accept sibling folders such as
    # "/workspace-old", and would follow symlinks that point outside.
    if not _is_inside_workspace(safe_path):
        raise ValueError(f"Path traversal denied for filename: {filename}")
    return safe_path

def manage_workspace_files(
    action: str,
    source_path: str,
    target_path: Optional[str] = None,
    content: Optional[str] = None,
    target_text: Optional[str] = None,
    replacement_text: Optional[str] = None,
    line_start: int = 1,
    line_count: int = 200,
    pattern: Optional[str] = None
) -> str:
    """
    Unified workspace filesystem manager for exploring, viewing, editing, 
    organizing, searching, archiving, and inspecting all project directories.
    
    Args:
        action: Choose from:
            - 'list': Lists folder contents.
            - 'read': Reads a text/markdown/code file within a line window.
            - 'write': Creates or overwrites a text/markdown/code file.
            - 'append': Adds new content to the very end of an existing file.
            - 'patch': Surgically replaces a specific text string block.
            - 'mkdir': Creates a new folder or directory structure.
            - 'copy': Copies a file/folder from source_path to target_path.
            - 'move': Moves or renames a file/folder from source_path to target_path.
            - 'delete': Permanently deletes a file or directory tree.
            - 'search': Globally scans the workspace for files matching a wildcard pattern.
            - 'archive': Zips a directory (source_path) into a .zip file (target_path).
            - 'extract': Unzips an archive (source_path) into a directory (target_path).
            - 'metadata': Retrieves size, creation time, and type diagnostics for a target.
        source_path: The file or directory to act upon. For 'list' or 'search', use '.' or a folder name.
        target_path: Required for 'copy', 'move', 'archive', and 'extract' actions.
        content: Required for 'write' and 'append' actions. The full text content to store.
        target_text: Required for 'patch' action. The exact string block to substitute.
        replacement_text: Required for 'patch' action. The new text block to insert.
        line_start: Optional for 'read' action. Line number to start reading from (default 1).
        line_count: Optional for 'read' action. Number of lines to pull (default 200).
        pattern: Required for 'search' action. Wildcard pattern (e.g., '*.png', '**/*.ts').
    """
    try:
        src_abs = _get_safe_path(source_path)
        
        if action == 'list':
            if not os.path.isdir(src_abs):
                return f"Error: '{source_path}' is not a valid directory."
            items = os.listdir(src_abs)
            formatted = [f"📁 {i}/" if os.path.isdir(os.path.join(src_abs, i)) else f"📄 {i}" for i in items]
            loc = source_path if source_path != '.' else 'Root'
            return f"Contents of [{loc}]: {', '.join(formatted)}" if formatted else f"[{loc}] is empty."

        elif action == 'read':
            if not os.path.isfile(src_abs):
                return f"Error: File not found at '{source_path}'."
            with open(src_abs, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            start_idx = max(0, line_start - 1)
            snippet = "".join(lines[start_idx : start_idx + line_count])
            actual_end = start_idx + len(lines[start_idx : start_idx + line_count])
            return f"--- {source_path} (Lines {line_start} to {actual_end}) ---\n{snippet}"

        elif action == 'write':
            if content is None: return "Error: 'content' parameter is required for a write action."
            os.makedirs(os.path.dirname(src_abs), exist_ok=True)
            with open(src_abs, 'w', encoding='utf-8') as f: f.write(content)
            return f"Successfully wrote {len(content)} characters to '{source_path}'."

        elif action == 'append':
            if content is None: return "Error: 'content' parameter is required for an append action."
            os.makedirs(os.path.dirname(src_abs), exist_ok=True)
            with open(src_abs, 'a', encoding='utf-8') as f: f.write(content)
            return f"Successfully appended {len(content)} characters to '{source_path}'."

        elif action == 'patch':
            if not target_text or replacement_text is None:
                return "Error: Both 'target_text' and 'replacement_text' are required for patching."
            if not os.path.isfile(src_abs): return f"Error: File not found at '{source_path}'."
            with open(src_abs, 'r', encoding='utf-8') as f: file_data = f.read()
            if target_text not in file_data:
                return "Error: target_text not found in file. Ensure exact character matching."
            with open(src_abs, 'w', encoding='utf-8') as f:
                f.write(file_data.replace(target_text, replacement_text, 1))
            return f"Successfully patched '{source_path}'."

        elif action == 'mkdir':
            os.makedirs(src_abs, exist_ok=True)
            return f"Successfully created directory structure: {source_path}"

        elif action == 'delete':
            if source_path in ['.', '/', '\\', ''] or os.path.realpath(src_abs) == _workspace_root():
                return "Critical Error: Cannot delete root workspace."
            if not os.path.exists(src_abs): return f"Delete failed: '{source_path}' does not exist."
            if os.path.isdir(src_abs):
                shutil.rmtree(src_abs)
                return f"Successfully deleted directory tree: {source_path}"
            else:
                os.remove(src_abs)
                return f"Successfully deleted file: {source_path}"

        elif action in ['copy', 'move']:
            if not target_path: return f"Error: 'target_path' is required for '{action}'."
            tgt_abs = _get_safe_path(target_path)
            if not os.path.exists(src_abs): return f"Operation failed: Source '{source_path}' does not exist."
            os.makedirs(os.path.dirname(tgt_abs), exist_ok=True)
            if action == 'copy':
                shutil.copytree(src_abs, tgt_abs, dirs_exist_ok=True) if os.path.isdir(src_abs) else shutil.copy2(src_abs, tgt_abs)
                return f"Successfully copied '{source_path}' to '{target_path}'."
            else:
                shutil.move(src_abs, tgt_abs)
                return f"Successfully moved/renamed '{source_path}' to '{target_path}'."

        elif action == 'archive':
            if not target_path: return "Error: 'target_path' (e.g., 'backup.zip') is required."
            tgt_abs = _get_safe_path(target_path)
            if not os.path.isdir(src_abs): return f"Error: Source '{source_path}' must be a directory to archive."
            base_name = os.path.splitext(tgt_abs)[0]
            shutil.make_archive(base_name, 'zip', src_abs)
            return f"Successfully archived '{source_path}' into '{base_name}.zip'."

        elif action == 'extract':
            if not target_path: return "Error: 'target_path' (destination folder) is required."
            tgt_abs = _get_safe_path(target_path)
            if not os.path.isfile(src_abs): return f"Error: Archive '{source_path}' not found."
            os.makedirs(tgt_abs, exist_ok=True)
            # Zip extraction already refuses unsafe member paths; tar needs the 'data' filter.
            extract_options = {} if zipfile.is_zipfile(src_abs) else {"filter": "data"}
            shutil.unpack_archive(src_abs, tgt_abs, **extract_options)
            return f"Successfully extracted '{source_path}' into '{target_path}'."

        elif action == 'search':
            if not pattern: return "Error: 'pattern' parameter is required for search."
            search_pattern = os.path.join(src_abs, pattern)
            safe_base = os.path.abspath(WORKSPACE_DIR)
            if not _is_inside_workspace(os.path.abspath(search_pattern)):
                return "Security Error: Search pattern attempts to traverse outside the sandbox."
            matched_paths = [p for p in glob.glob(search_pattern, recursive=True) if _is_inside_workspace(p)]
            if not matched_paths: return f"No files matched '{pattern}' in '{source_path}'."
            relative_matches = [f"📁 {os.path.relpath(p, safe_base)}/" if os.path.isdir(p) else f"📄 {os.path.relpath(p, safe_base)}" for p in matched_paths]
            return f"Found {len(relative_matches)} match(es) for '{pattern}':\n" + "\n".join(relative_matches)

        elif action == 'metadata':
            if not os.path.exists(src_abs): return f"Error: '{source_path}' does not exist."
            stat = os.stat(src_abs)
            size_bytes = stat.st_size
            size_str = f"{size_bytes} B" if size_bytes < 1024 else f"{round(size_bytes/1024, 2)} KB" if size_bytes < 1024**2 else f"{round(size_bytes/(1024**2), 2)} MB"
            return "\n".join([
                f"=== Metadata Profile: {source_path} ===",
                f"  • Type:          {'Directory' if os.path.isdir(src_abs) else (mimetypes.guess_type(src_abs)[0] or 'Unknown Binary/Text')}",
                f"  • Size:          {size_str}",
                f"  • Last Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))}"
            ])
        else:
            return f"Error: Unsupported action '{action}'."
            
    except Exception as e:
        return f"Error executing file management action '{action}': {str(e)}"

# ==============================================================================
# IMAGE TOOL: one tool, four actions
# ==============================================================================
def _transform_image(filename: str, action: str, output_filename: str, width: Optional[int] = None, height: Optional[int] = None) -> str:
    try:
        with Image.open(_get_safe_path(filename)) as img:
            if action == 'resize':
                if not width or not height: return "Error: Both 'width' and 'height' must be provided."
                img = img.resize((width, height))
            elif action == 'convert_to_png': img = img.convert("RGBA")
            else: return f"Error: Unsupported action '{action}'."
            img.save(_get_safe_path(output_filename))
            return f"Image transformed via '{action}' and saved to {output_filename}."
    except Exception as e: return f"Error processing image {filename}: {str(e)}"

def _generate_comfy_image(prompt: str, negative_prompt: str = "", width: int = 1024, height: int = 1024) -> str:
    workflow = {
      "3": {"class_type": "KSampler", "inputs": {"cfg": 4.5, "denoise": 1, "latent_image": ["5", 0], "model": ["4", 0], "negative": ["7", 0], "positive": ["6", 0], "sampler_name": "dpmpp_2m_sde", "scheduler": "karras", "seed": random.randint(1, 99999999999999), "steps": 35}},
      "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "juggernautXL_ragnarok.safetensors"}},
      "5": {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 1, "height": height, "width": width}},
      "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": prompt}},
      "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": negative_prompt}},
      "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
      "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lyra_gen", "images": ["8", 0]}}
    }
    data = json.dumps({"prompt": workflow}).encode('utf-8')
    req = urllib.request.Request(f"{COMFYUI_URL}/prompt", data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            prompt_id = json.loads(response.read()).get("prompt_id")
        if not prompt_id: return "Error: Failed to queue image generation in ComfyUI."
        for _ in range(120):
            time.sleep(1)
            with urllib.request.urlopen(urllib.request.Request(f"{COMFYUI_URL}/history/{prompt_id}"), timeout=5) as hist_resp:
                history = json.loads(hist_resp.read())
                if prompt_id in history and "9" in history[prompt_id].get("outputs", {}):
                    filename = history[prompt_id]["outputs"]["9"]["images"][0]["filename"]
                    save_path = _get_safe_path(os.path.join("images", filename))
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)  # the images folder may not exist yet
                    urllib.request.urlretrieve(f"{COMFYUI_URL}/view?filename={urllib.parse.quote(filename)}", save_path)
                    return f"Successfully generated image and saved to workspace as: images/{filename}"
        return "Error: ComfyUI image generation timed out."
    except Exception as e: return f"Error communicating with ComfyUI: {str(e)}"

def _view_image(filename: str) -> str:
    try:
        # Strip a leading "workspace/" the model sometimes adds. Only a leading one:
        # a folder really named "workspace" further down the path is left alone.
        normalized = filename.replace("\\", "/")
        if normalized.startswith("workspace/"):
            normalized = normalized[len("workspace/"):]
        absolute_path = _get_safe_path(normalized)
        if not os.path.exists(absolute_path): return f"Error: File not found at '{absolute_path}'"
        return f"LOCAL_WORKSPACE_IMAGE:{absolute_path}"
    except Exception as e: return f"Error resolving image path: {str(e)}"

async def manage_workspace_images(
    action: str,
    filename: Optional[str] = None,
    output_filename: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    prompt: Optional[str] = None,
    negative_prompt: str = ""
) -> str:
    """
    Unified workspace image tool for generating, viewing, and transforming images.

    Args:
        action: Choose from:
            - 'generate': Creates a new image from a text prompt via ComfyUI (Juggernaut XL Ragnarok)
              and saves it into the workspace 'images' folder. Can take up to two minutes.
            - 'view': Loads an image file from the workspace into your multimodal vision pipeline so
              you can see it. Use this whenever asked to look at, inspect, or describe a picture.
            - 'resize': Resizes an image to 'width' x 'height' and saves it as 'output_filename'.
            - 'convert_to_png': Converts an image to PNG and saves it as 'output_filename'.
        filename: Required for 'view', 'resize', and 'convert_to_png'. The image's path in the workspace
                  (e.g., 'images/lyra_gen_00001_.png').
        output_filename: Required for 'resize' and 'convert_to_png'. Where to save the result.
        width: Required for 'resize'. Optional for 'generate' (default 1024).
        height: Required for 'resize'. Optional for 'generate' (default 1024).
        prompt: Required for 'generate'. Description of the image to create.
        negative_prompt: Optional for 'generate'. What the image should not contain.
    """
    clean_action = (action or "").strip().lower()

    if clean_action == 'generate':
        if not prompt or not prompt.strip():
            return "Error: 'prompt' is required for a 'generate' action."
        # Generation polls ComfyUI for up to two minutes; run it off the event loop
        # so the server keeps answering other requests meanwhile.
        return await asyncio.to_thread(
            _generate_comfy_image, prompt, negative_prompt or "", width or 1024, height or 1024
        )

    if clean_action == 'view':
        if not filename:
            return "Error: 'filename' is required for a 'view' action."
        return _view_image(filename)

    if clean_action in ['resize', 'convert_to_png']:
        if not filename:
            return f"Error: 'filename' is required for a '{clean_action}' action."
        if not output_filename:
            return f"Error: 'output_filename' is required for a '{clean_action}' action."
        return _transform_image(filename, clean_action, output_filename, width, height)

    return f"Error: Unsupported action '{action}'. Choose from: 'generate', 'view', 'resize', 'convert_to_png'."
