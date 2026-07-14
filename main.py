import os
import subprocess
import json
import glob
from telethon import TelegramClient, events

# --- CONFIGURATION ---
API_ID = 34801155        
API_HASH = 'd7846c4d0f2c343dd5b67c80d45409e8' 
BOT_TOKEN = '8949289098:AAHcTAeSeXUssgIfOV5hIewrVrb8MauBaTs' 

TARGET_CHANNEL = -1003895453478  
CHANNEL_TEXT_NAME = "@AllstoryFM2"  
# ---------------------

client = TelegramClient('multi_compressor_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

def cleanup_all_inputs():
    print("Automatic server cleanup chalu ho raha hai...")
    for input_file in glob.glob("input_*"):
        try: os.remove(input_file)
        except: pass
    for comp_file in glob.glob("compressed_*"):
        try: os.remove(comp_file)
        except: pass

def get_audio_duration(file_path):
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:key=value -of json "{file_path}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except Exception:
        return 0

@client.on(events.NewMessage(incoming=True))
async def handle_new_message(event):
    is_audio = event.message.audio is not None
    is_video = event.message.video is not None

    if not (is_audio or is_video):
        return  

    status_msg = await event.reply("File mil gayi hai, dynamic calculation chalu hai... ⏳")
    
    orig_filename = event.message.file.name or ("video.mp4" if is_video else "audio.mp3")
    
    # Filename se purana extension hata kar hamesha .mp3 set karne ke liye
    base_name, _ = os.path.splitext(orig_filename)
    if is_audio:
        # File name ko .mp3 mein convert kiya
        out_filename = f"{base_name}.mp3"
    else:
        out_filename = f"{base_name}.mp4"

    input_path = f"input_{orig_filename}"
    output_path = f"compressed_{out_filename}"

    try:
        await event.download_media(file=input_path)
        final_bitrate = 64 

        if is_audio:
            duration_seconds = get_audio_duration(input_path)
            duration_minutes = duration_seconds / 60

            if duration_seconds > 0:
                target_size_bytes = 11 * 1024 * 1024  
                calculated_bitrate = int((target_size_bytes * 8) / (duration_seconds * 1024))
                
                if calculated_bitrate > 96: final_bitrate = 96
                elif calculated_bitrate < 24: final_bitrate = 24
                else: final_bitrate = calculated_bitrate
            else:
                input_size_mb = os.path.getsize(input_path) / (1024 * 1024)
                if input_size_mb > 50: final_bitrate = 32  
                elif input_size_mb > 25: final_bitrate = 48
                else: final_bitrate = 64

            bitrate_str = f"{final_bitrate}k"
            await status_msg.edit(f"Processing Audio | Bitrate Auto-Set: {bitrate_str}... ⏳")
            
            # -acodec libmp3lame joda hai taaki strictly MP3 hi bane
            cmd = f'ffmpeg -y -i "{input_path}" -acodec libmp3lame -b:a {bitrate_str} "{output_path}"'
        
        elif is_video:
            cmd = f'ffmpeg -y -i "{input_path}" -vcodec libx264 -crf 28 -preset faster -acodec mp3 -b:a 64k "{output_path}"'

        subprocess.run(cmd, shell=True, check=True)

        file_size_bytes = os.path.getsize(output_path)
        file_size_mb = round(file_size_bytes / (1024 * 1024), 2)

        custom_caption = (
            f">>JOIN > {CHANNEL_TEXT_NAME} 🔥\n"
            f"✅✨\n\n"
            f"👉 FILE SIZE :- {file_size_mb} MB 👑\n"
            f"🔥"
        )

        await client.send_file(TARGET_CHANNEL, file=output_path, caption=custom_caption, supports_streaming=True if is_video else False)
        await status_msg.edit("Done! ✅ File channel mein bhej di gayi hai.")

    except Exception as e:
        await status_msg.edit(f"Kuch galti hui: {str(e)}")
    
    finally:
        cleanup_all_inputs()

print("Bot ready hai...")
client.run_until_disconnected()
