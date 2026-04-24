import os
import asyncio
import uuid
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
import edge_tts
from mutagen.mp3 import MP3

app = Flask(__name__)

# 確保音檔暫存資料夾存在
if not os.path.exists('static'):
    os.makedirs('static')

# ================= 讀取環境變數 =================
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 初始化 Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# ================= 路由設定 =================
@app.route("/", methods=['GET'])
def hello():
    return "Tutor Bot Server is running on cloud!"

# 開放讓 LINE 抓取音檔的專屬路由
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

# ================= 核心邏輯 =================
# 非同步生成語音檔的函數
async def create_audio(text, filepath):
    # 這裡使用包含美式英文與中文混合的女聲，你也可以換成你熟悉的 zh-TW-YunJheNeural
    communicate = edge_tts.Communicate(text, "zh-TW-HsiaoChenNeural", rate="+0%")
    await communicate.save(filepath)

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    
    # 1. 向 Gemini 請求回覆 (家教 Prompt)
    prompt = f"""
    你現在是一位親切的英文家教。
    請根據學生的輸入「{user_message}」進行回應。
    如果是單字，請提供例句；如果是文法錯誤，請糾正；如果是中文，請教他怎麼用英文表達。
    注意：請務必只用「純文字」回覆，**絕對不要使用任何 Markdown 符號（如 ** 或 * 或 #）**，否則語音系統會把符號唸出來。
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    ai_reply_text = response.text

    # 2. 生成語音檔
    # 使用 uuid 產生隨機檔名避免衝突
    filename = f"{uuid.uuid4()}.mp3"
    filepath = os.path.join('static', filename)
    
    # 執行音檔生成
    asyncio.run(create_audio(ai_reply_text, filepath))
    
    # 讀取音檔長度 (LINE API 必須要有毫秒長度)
    audio = MP3(filepath)
    duration_ms = int(audio.info.length * 1000)
    
    # 動態取得當前伺服器的網址 (Request Host)
    host_url = request.host_url.rstrip('/')
    audio_url = f"{host_url}/audio/{filename}"

    # 3. 推播文字與語音回 LINE
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text=ai_reply_text),
                    AudioMessage(original_content_url=audio_url, duration=duration_ms)
                ]
            )
        )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
