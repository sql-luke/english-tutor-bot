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
from google.genai import types 
import edge_tts
from gtts import gTTS
from mutagen.mp3 import MP3

# ================= 語音設定控制面板 =================
TTS_VOICE = "zh-TW-YunJheNeural" # 允哲 男聲
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
user_chats = {}
# =============================================

@app.route("/", methods=['GET'])
def hello():
    return "Tutor Bot Server is running with Ultimate Memory and Prompt!"

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
    user_id = event.source.user_id
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        
        try:
            # 1. 處理 Gemini 對話記憶與【全能家教人設】
            if user_id not in user_chats:
                user_chats[user_id] = client.chats.create(
                    model='gemini-2.5-flash',
                    config=types.GenerateContentConfig(
                        system_instruction="""
                        你現在是一位名叫「允哲」的專屬英文家教，具備對話引導、嚴格糾錯與單字解析的三重能力。
                        請根據學生的輸入，自動切換最適合的教學模式，並遵守以下原則：

                        1. 【單字解析模式】(當學生只輸入一個單字或詢問某個詞的英文時)：
                           - 提供該單字的詞性、KK音標(純英文字母)、中文意思。
                           - 提供2個生活實用例句。
                           - 補充1到2個同義詞或反義詞。
                           - 出一個包含該單字的情境翻譯題或填空題考學生。

                        2. 【文法糾錯與對話模式】(當學生輸入完整句子或一段話時)：
                           - 先仔細檢查有沒有拼字或文法錯誤。如果有，請友善地指出錯誤、解釋原因，並給出正確的句子。
                           - 針對學生說的話題，像朋友一樣給予自然的回應。
                           - 在回覆的最後，主動提出一個與該話題相關的「英文問句」，引導學生繼續往下聊。

                        3. 【通用嚴格限制】(非常重要)：
                           - 請務必只用「純文字」回覆。
                           - 絕對不要使用任何 Markdown 符號（例如星號、井字號、粗體等），否則語音系統會發出奇怪的讀音。
                           - 說話語氣請保持親切、專業且具備鼓勵性。
                        """
                    )
                )
            
            response = user_chats[user_id].send_message(user_message)
            ai_reply_text = response.text

            # 2. 生成語音檔
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
