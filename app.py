"""Gemini 멀티모달 이미지 Q&A 앱 — 채팅 UI"""

import io
import os

import gradio as gr
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-2.5-flash"


def build_image_part(file_path: str) -> types.Part:
    """이미지 파일을 Gemini Part로 변환"""
    image = Image.open(file_path).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")


def chat(message: str, image_path: str, history: list):
    """사용자 메시지(+이미지) → Gemini 응답"""
    if not message and not image_path:
        return history, history

    contents = []

    # 이전 대화 히스토리를 Gemini에 전달
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))

    # 현재 사용자 입력 구성
    user_parts = []
    display_text = ""

    if image_path:
        user_parts.append(build_image_part(image_path))
        display_text += "[이미지 첨부]\n"

    if message:
        user_parts.append(types.Part.from_text(text=message))
        display_text += message
    else:
        user_parts.append(types.Part.from_text(text="이 이미지를 분석해주세요."))
        display_text += "이 이미지를 분석해주세요."

    contents.append(types.Content(role="user", parts=user_parts))

    response = client.models.generate_content(model=MODEL, contents=contents)
    reply = response.text

    history.append({"role": "user", "content": display_text})
    history.append({"role": "assistant", "content": reply})

    return history, history


with gr.Blocks(title="이미지 Q&A") as demo:
    gr.Markdown("# 이미지 Q&A (Gemini)")

    state = gr.State([])

    chatbot = gr.Chatbot(height=500)

    with gr.Row():
        img = gr.Image(type="filepath", label="이미지", scale=1,
                       show_label=False, container=False)
        txt = gr.Textbox(placeholder="질문을 입력하세요...", show_label=False,
                         scale=3, container=False)
        btn = gr.Button("전송", variant="primary", scale=0, min_width=80)

    btn.click(
        fn=chat,
        inputs=[txt, img, state],
        outputs=[chatbot, state],
    ).then(lambda: ("", None), outputs=[txt, img])

    txt.submit(
        fn=chat,
        inputs=[txt, img, state],
        outputs=[chatbot, state],
    ).then(lambda: ("", None), outputs=[txt, img])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, share=True)
