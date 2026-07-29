"""
Gradio UI — the deployable "app" for Hugging Face Spaces (free tier).
User uploads clips, clicks Analyze, downloads the resulting CSV/metadata.
"""
import os
import shutil
import tempfile
import gradio as gr

from core import process_broll_folder


def run_analysis(files, progress=gr.Progress(track_tqdm=False)):
    if not files:
        return "No files uploaded.", None, None

    work_dir = tempfile.mkdtemp(prefix="broll_")
    for f in files:
        shutil.copy(f, os.path.join(work_dir, os.path.basename(f)))

    logs = []

    def log(msg):
        logs.append(msg)

    csv_path = process_broll_folder(work_dir, log=log)
    metadata_path = os.path.join(work_dir, "des", "broll_metadata.json")

    log_text = "\n".join(logs)
    csv_out = csv_path if os.path.isfile(csv_path) else None
    meta_out = metadata_path if os.path.isfile(metadata_path) else None
    return log_text, csv_out, meta_out


with gr.Blocks(title="B-Roll Analyzer") as demo:
    gr.Markdown(
        "# 🎬 B-Roll Analyzer\n"
        "Upload short (5-6s) narrator clips. Gemini watches each one and "
        "produces a `broll_auto.csv` + `broll_metadata.json` ready for your "
        "video pipeline's asset library."
    )
    file_input = gr.File(file_count="multiple", label="Upload clips (.mp4, .mov, .mkv, .webm)")
    run_btn = gr.Button("Analyze", variant="primary")
    log_output = gr.Textbox(label="Log", lines=15)
    with gr.Row():
        csv_output = gr.File(label="broll_auto.csv")
        meta_output = gr.File(label="broll_metadata.json")

    run_btn.click(run_analysis, inputs=[file_input], outputs=[log_output, csv_output, meta_output])

if __name__ == "__main__":
    demo.launch()
