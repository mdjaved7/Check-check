import os
import re
import logging
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, error as ID3Error
from mutagen.mp4 import MP4, MP4Cover

# लॉगिंग सेट करें
logging.basicConfig(level=logging.INFO)

# --- वेब सर्वर (Railway के लिए) ---
def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# --- बॉट सेटअप ---
API_ID = 34801155             
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8"   
BOT_TOKEN = "8918721301:AAGQomTKJ5vtViPRyAhHAZ51_eEmJk1v25I" 

bot = TelegramClient('tagger_bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# फाइल स्टोर करने के लिए
pending_files = {}

@bot.on(events.NewMessage(incoming=True, pattern='/start'))
async def start(event):
    await event.respond("👋 नमस्ते! मैं ऑडियो टैगर बॉट हूँ।\n\n1. अपना **ऑडियो** भेजें।\n2. अपनी **फोटो** भेजें।\nमैं अपने आप उन्हें जोड़ दूंगा!")

@bot.on(events.NewMessage(incoming=True))
async def handle_message(event):
    if not event.message.media: return
    chat_id = event.chat_id
    
    # 1. ऑडियो फाइल चेक करें
    if event.message.file and event.message.file.ext.lower() in ['.mp3', '.m4a']:
        if chat_id not in pending_files: pending_files[chat_id] = {}
        pending_files[chat_id]['audio'] = event.message
        await event.respond("✅ ऑडियो मिल गया! अब कृपया **फोटो** भेजें।")
        
    # 2. फोटो फाइल चेक करें
    elif event.message.photo:
        if chat_id not in pending_files: pending_files[chat_id] = {}
        pending_files[chat_id]['photo'] = event.message
        await event.respond("✅ फोटो मिल गया! अब कृपया **ऑडियो** भेजें।")

    # दोनों मिल गए तो प्रोसेस करें
    if chat_id in pending_files and 'audio' in pending_files[chat_id] and 'photo' in pending_files[chat_id]:
        data = pending_files.pop(chat_id)
        await process_and_send(event, data['audio'], data['photo'])

async def process_and_send(event, audio_msg, photo_msg):
    status = await event.respond("⚡ प्रोसेसिंग शुरू हो रही है...")
    
    # डाउनलोड्स
    audio_path = os.path.join(".", audio_msg.file.name or "audio.mp3")
    await status.edit("📥 फ़ाइल डाउनलोड हो रही है...")
    await bot.download_media(audio_msg, audio_path)
    image_data = await bot.download_media(photo_msg, bytes)

    # मेटाडेटा
    title = f"Ep {re.search(r'\d+', audio_msg.file.name or '').group() or 'Unknown'}"
    
    await status.edit("✍️ टैगिंग हो रही है...")
    ext = os.path.splitext(audio_path)[1].lower()
    
    success = False
    if ext == '.mp3':
        audio = MP3(audio_path, ID3=ID3)
        try: audio.add_tags()
        except: pass
        audio.tags.add(TIT2(text=title))
        audio.tags.add(APIC(data=image_data, mime='image/jpeg', type=3))
        audio.save()
        success = True
    elif ext == '.m4a':
        audio = MP4(audio_path)
        audio["\xa9nam"] = title
        audio["covr"] = [MP4Cover(image_data, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        success = True

    if success:
        await status.edit("📤 अपलोड हो रहा है...")
        await bot.send_file(event.chat_id, audio_path, caption=f"✅ {title} तैयार है!")
        await status.delete()
    
    if os.path.exists(audio_path): os.remove(audio_path)

if __name__ == "__main__":
    bot.run_until_disconnected()
    
