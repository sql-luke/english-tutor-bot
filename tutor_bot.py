import os
import uuid
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
from gtts import gTTS  # 新增：Google 語音套件
from mutagen.mp3 import MP3

app = Flask(__name__)

if not os.path.exists('static'):
    os.makedirs('static')

CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
client = genai.Client(api_key=GEMINI_API_KEY)

@app.route("/", methods=['GET'])
def hello():
    return "Tutor Bot Server is running on cloud!"

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

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        
        try:
            # 1. 向 Gemini 請求回覆
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

            # 2. 生成語音檔 (改用 gTTS)
            filename = f"{uuid.uuid4()}.mp3"
            filepath = os.path.join('static', filename)
            
            # lang='zh-tw' 支援中文發音，遇到英文時也會自動切換成英文發音
            tts = gTTS(text=ai_reply_text, lang='zh-tw')
            tts.save(filepath)
            
            # 讀取音檔長度
            audio = MP3(filepath)
            duration_ms = int(audio.info.length * 1000)
            
            # 3. 修正網址：強制替換 http 為 https
            host_url = request.host_url.replace("http://", "https://").rstrip('/')
            audio_url = f"{host_url}/audio/{filename}"

            # 4. 推播文字與語音回 LINE
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
            error_msg = f"系統發生錯誤了！原因如下：\n{str(e)}\n\n請檢查 Render Logs 或截圖給開發協助者。"
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
