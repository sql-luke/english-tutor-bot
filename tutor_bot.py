import os
import uuid
import asyncio
import traceback
from flask import Flask, request, abort, send_from_directory
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    AudioMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from google import genai
from google.genai import types # 新增：用來設定系統提示詞的套件
import edge_tts
from gtts import gTTS
from mutagen.mp3 import MP3

# ================= 語音設定控制面板 =================
TTS_VOICE = "zh-TW-YunJheNeural"
TTS_RATE = "+0%"
# ====================================================

app = Flask(__name__)

if not os.path.exists('static'):
    os.makedirs('static')

CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
client = genai.Client(api_key=GEMINI_API_KEY)

# ================= 記憶體字典 =================
# 用來儲存每一個 LINE 用戶的專屬對話紀錄
user_chats = {}
# =============================================

@app.route("/", methods=['GET'])
def hello():
    return "Tutor Bot Server is running with Memory capability!"

@app.route("/audio/<filename>", methods=['GET'])
def serve_audio(filename):
    return send_from_directory('static', filename)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

async def create_edge_audio(text, filepath):
    communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
    await communicate.save(filepath)

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    
    # 取得傳送訊息的 LINE 使用者 ID
    user_id = event.source.user_id
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        
        try:
            # 1. 處理 Gemini 對話記憶
            # 如果這個使用者是第一次傳訊息，幫他開一個專屬的「聊天室」並注入家教人設
            if user_id not in user_chats:
                user_chats[user_id] = client.chats.create(
                    model='gemini-2.5-flash',
                    config=types.GenerateContentConfig(
                        system_instruction="""
                        你現在是一位親切的英文家教。
                        請根據學生的輸入進行回應。
                        如果是單字，請提供例句；如果是文法錯誤，請糾正；如果是中文，請教他怎麼用英文表達。
                        注意：請務必只用「純文字」回覆，**絕對不要使用任何 Markdown 符號（如 ** 或 * 或 #）**，否則語音系統會把符號唸出來。
                        """
                    )
                )
            
            # 將學生的訊息傳送到該專屬聊天室 (Gemini 會自動帶入先前的歷史紀錄)
            response = user_chats[user_id].send_message(user_message)
            ai_reply_text = response.text

            # 2. 生成語音檔 (雙重保險機制)
            filename = f"{uuid.uuid4()}.mp3"
            filepath = os.path.join('static', filename)
            
            try:
                asyncio.run(create_edge_audio(ai_reply_text, filepath))
            except Exception as e:
                print(f"Edge TTS 失敗，啟動 gTTS: {e}")
                tts = gTTS(text=ai_reply_text, lang='zh-tw')
                tts.save(filepath)
            
            # 讀取音檔長度
            audio = MP3(filepath)
            duration_ms = int(audio.info.length * 1000)
            
            # 3. 修正網址並推播
            host_url = request.host_url.replace("http://", "https://").rstrip('/')
            audio_url = f"{host_url}/audio/{filename}"

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(text=ai_reply_text),
                        AudioMessage(original_content_url=audio_url, duration=duration_ms)
                    ]
                )
            )
            
        except Exception as e:
            error_msg = f"系統發生錯誤了！原因如下：\n{str(e)}"
            print(traceback.format_exc())
            try:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=error_msg)]
                    )
                )
            except:
                pass

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
