import logging
import json
import os
import time
import asyncio
import subprocess

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, PeerIdInvalid, ChatAdminRequired, MessageNotModified

# ---- CONFIGURATION ----
API_ID = 34801155          
API_HASH = "d7846c4d0f2c343dd5b67c80d45409e8" 
BOT_TOKEN = "8961976960:AAHClYL_3wisXbO3eYsnISMe3xAF5Js0hL8"
OWNER_ID = 6598432032


app = Client("ultimate_forward_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ... (baaki sabhi functions load_json, save_json, safe_api_call wahi rahenge)

# Helper function for Compression
async def compress_audio(input_file):
    output_file = "comp_" + input_file
    # FFmpeg command: 64k bitrate par compress karega
    cmd = [
        'ffmpeg', '-y', '-i', input_file,
        '-b:a', '64k', '-ac', '1',  # 64kbps, mono (size bahut kam ho jayega)
        output_file
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    return output_file

# ---- UPDATED SEND_BATCH FUNCTION ----
@app.on_callback_query()
async def handle_buttons(client, callback_query):
    # ... (purana logic wahi rakhein)
    
    if data == "send_batch":
        u_state = user_states.get(user_id)
        # ... (check queue logic)
        
        await safe_api_call(callback_query.message.edit_reply_markup, reply_markup=None)
        u_state["files"].sort(key=lambda x: x["msg_id"])
        total_files = len(u_state["files"])
        target_chat_id = int(u_state["target_channel"])
        
        success_count = 0
        
        for index, file_data in enumerate(u_state["files"], 1):
            await safe_api_call(callback_query.message.edit_text, f"📥 Processing & Compressing {index}/{total_files}...")
            
            msg = await client.get_messages(int(file_data["from_chat_id"]), int(file_data["msg_id"]))
            file_path = await client.download_media(msg)
            
            if file_path:
                # 1. Compress
                compressed_path = await compress_audio(file_path)
                
                # 2. Upload
                await client.send_audio(
                    chat_id=target_chat_id,
                    audio=compressed_path,
                    caption=f"🎧 Compressed: {msg.caption or 'File'}"
                )
                
                # 3. Cleanup
                if os.path.exists(file_path): os.remove(file_path)
                if os.path.exists(compressed_path): os.remove(compressed_path)
                
                success_count += 1
            
            await asyncio.sleep(1)

        await safe_api_call(callback_query.message.edit_text, f"✅ Batch Finished! {success_count} files sent.")
        # ... (reset queue logic)

# ... (baki ka code wahi rahega)
