import re
import io
import os
import asyncio
import aiohttp
import tempfile
import urllib.parse
from PIL import Image as PILImage, ImageSequence, ImageFilter, ImageOps, ImageEnhance
from astrbot.api.event import filter
from astrbot.api.all import *
from astrbot.api import logger
import astrbot.api.message_components as Comp

# 尝试导入 imageio
try:
    import imageio
except ImportError:
    imageio = None


# --- 达妮娅(娅娅)风格化文案表 ---
# key -> 默认文案模板。模板支持 {nick} 自定义昵称、{factor}/{fps}/{n}/{t}/{level}/{fmt} 等占位符。
# 不开启 danya_style 时，_danya() 会回退到 *_raw 兜底（若未提供则原样返回 key）。
DANYA_TEXTS = {
    # 通用
    "need_image":          "咦？我没看到图呀～发张图过来，或者回复一张图，{nick}这就帮你看看～",
    "need_gif":            "咦？我看不到动图呀～发个GIF或者回复一个给{nick}吧～",
    "need_gif_help":       "咦？我看不到动图呀～发个GIF或者回复一个吧～\n用法：gif变速 2x（倍速）/ 30fps（帧率）",
    "need_video":          "咦？没看到视频呢～回复一个视频消息，或者把视频链接发给我吧～",
    "image_dl_fail":       "啊……图片没拿到呢。要不，忘掉它，再试一次？",
    "video_dl_fail":       "啊……没拿到呢。要不，忘掉它，再试一次？",
    "video_too_big":       "诶呀，视频太丰满了～泡泡装不下，换个小一点的再来吧？",
    "video_resolve_fail":  "这个地址我读不懂呢：{src}　要不换个方式给{nick}？",
    "video_internal_err":  "唔……内部出小差了：{err}",
    "video_start_overflow":  "开始时间超出范围啦，{nick}没法从这里截起～",
    "video_no_frames":      "咦……一帧也没抽到呢，要不要换个区间？",
    "video_format_unsupport": "❌ 服务器还没有装好 imageio 呢，{nick}暂时没法用这个～",
    "proc_err":            "啊……处理出错了：{err}",

    # 线稿
    "lineart_proc":        "稍微等等我哦……困意上来了，不过这就帮你转线稿～",
    "lineart_dl_fail":     "图没下来呢……链接娅娅够不到，要先能访问才行哦～",
    "lineart_ok":          "好啦～转完了。哈哈，是不是很意外？",
    "lineart_fail":        "唔……转不动呢，这图是不是有点特别？",

    # 视频
    "video_requesting":   "让{nick}找找它的地址……稍等一下～",
    "video_accepting":     "收到啦～交给我吧。先去打个盹也行哦♪\n区间：{range}　缩放：{scale}\n格式：{fmt}",
    "video_too_long_warn": "(限时{s}s)",   # 内部插入到msg；保留极简，避免破坏 info 结构
    "video_truncate_warn": "[帧多，悄悄抽]",
    "video_convert_ok":    "✅ 转换成功",
    "video_transmute_big": "⚠️ 初次体积{sz}MB有点丰满了……{nick}帮它泡泡裹紧一点～\n",
    "video_compress_fail": "⚠️ 瘦身失败啦({sz}MB)，那就给你原味那版～\n",

    # 变速
    "speed_proc_acc":      "泡泡兜一圈，让它冲快一点～{factor} 倍速，出发！",
    "speed_proc_dec":      "慢下来，慢下来……像打盹一样慢～{factor} 倍速。",
    "speed_proc_fps":      "把节奏调到 {fps:.0f} fps……步子要轻轻的哦～",
    "speed_done_acc":      "哼哼～加速好了。{factor}x加速 | 原 {src:.1f}fps → 等效 {dst:.1f}fps",
    "speed_done_dec":      "慢慢悠悠地好啦～{factor}x减速 | 原 {src:.1f}fps → 等效 {dst:.1f}fps",
    "speed_done_plain":    "搞定啦～{factor}x | 原 {src:.1f}fps → 等效 {dst:.1f}fps",
    "speed_done_fps_mode": "搞定啦～目标 {dst:.0f}fps | 原 {src:.1f}fps",
    "speed_done_dropframes": "帧太多装不下了，我帮你悄悄抽掉一些～（抽到{kept}/{total}帧）",
    "speed_not_anim":      "它不会动呢……不动的东西，{nick}也变不出花样～",
    "speed_no_frames":     "嗯？没有有效帧呢……要不换一张再试？",
    "speed_speed_err":     "唔……{err}",
    "speed_fail":         "啊……变速失败：{info}",
    "speed_help_usage":    "咦？我看不到动图呀～发个GIF或者回复一个吧～\n用法：gif变速 2x（倍速）/ 30fps（帧率）",

    # 倒放
    "reverse_proc":        "倒带一下……有些事，倒回去看会比较幸福吗？",
    "reverse_done":        "倒着走完了。{n}帧，{t}s——其实，{nick}都记住了。",
    "reverse_not_anim":    "它不会动呢，倒过来，也还是不动呀～",
    "reverse_too_short":  "才一帧？这就没必要倒带啦～",
    "reverse_fail":        "诶……倒不了：{err}",
    "reverse_fail_info":  "诶……倒放失败：{info}",

    # 分解
    "decompose_proc":     "一帧一帧拆给你看哦，慢慢来，别急～",
    "decompose_not_anim": "它不会动呢，没法拆开哦～",
    "decompose_err":      "啊……分解出错：{err}",
    "decompose_fail":     "啊……分解失败：{info}",
    "decompose_frame_label": "第{n}帧",

    # 裁剪
    "crop_too_many":      "格子太密啦，娅娅数不过来～",
    "crop_fail":          "切不开呢……{err}",
    "crop_no_image":      "咦？没看到图呀～发张图给{nick}吧～",
    "crop_proc":          "正在切哦……样大小整齐的格子～",
    "crop_image_too_small": "❌ 图太小啦，切不开呢 {crop}",
    "too_small_grid":     "格子太小啦，{w}×{h} 装不下呢～",


    # 合成精灵图
    "make_proc_mode":     "尝尝{nick}的合成泡泡～算法{mode}，{r}×{c} 每帧 {dur}s",
    "make_ok_1":          "拼好啦～算法1 | {w}×{h} | {r}行{c}列",
    "make_ok_1_raw":      "✅ 合成成功\n算法1 | {w}x{h} | {r}行{c}列",
    "make_ok_2":          "拼好啦～算法2(透明+抖动优化) | {w}×{h} | {r}行{c}列",
    "make_ok_2_raw":      "✅ 合成成功\n算法2 | {w}x{h} | {r}行{c}列",
    "make_logic_err":     "合成里有点小乱哦：{err}",
    "make_crop_msg":      "\n✂️ 切边：上{t} 下{b} 左{l} 右{r}",
    "make_crop_invalid":  "\n⚠️ 切边设错了：{w}×{h} → {l},{u},{r},{d}",
    "make_crop_err":      "\n⚠️ 切边出错了：{err}",
    "make_fail":          "啊……拼不起来：{info}",

    # 多图合成
    "multi_collecting":   "正在收集泡泡～一张张捞起来……",
    "multi_dl_proc":      "{n} 张都拿到啦，捞起来正在拼，每帧 {dur}s～",
    "multi_need_more":    "图太少了呀～至少要回复几张图给{nick}哦（合并转发也行）",
    "multi_dl_fail":      "啊……图都下载失败了，要不换个方式再试？",
    "multi_done":         "好啦～拼好了，{n} 张图{nick}都装进泡泡里啦♪",
    "multi_canvas_hint":  "画布自适应，居中填充，温柔对待每张图～",
    "multi_ok":           "✅ 合成成功 ({n}张)",
    "multi_fail":         "诶……拼不起来：{err}",

    # 表情包做旧
    "age_no_image":       "咦？没看到图呀～做旧要先发图给{nick}哦（合并转发也可以）\n用法：表情包做旧 次数（建议 1~20）",
    "age_level_light":   "轻轻地做旧一点点～",
    "age_level_mid":     "明显地旧了些～它会静静看着别人，于是也泛起绿意",
    "age_level_heavy":   "变成真正的老照片了呢……有些事情，旧一点反而显得温柔",
    "age_level_extreme": "极限做旧——赛博遗产级别。要不……把它也忘掉？",
    "age_proc":          "让它一点点变旧吧，{n} 次传播，{level}～时间会冲淡一切的。",
    "age_done_static":   "做旧好啦～{n} 次传播，老照片哀愁感拉满♪（质量{q}%）",
    "age_done_gif":      "做旧好啦～动图 {n} 帧每帧都旧了，{m} 次传播的痕迹。",
    "age_done_hint":     "💡 {level}",
    "age_fail":          "唔……做旧失败：{err}",
    "age_anim_read_err": "动图帧读不出来呢……",

    # 表情帮助
    "help_expression":    "📦 表情包小课堂～（{nick}来教你）\n\n"
                          "1️⃣ 表情包做旧：把图/动图做成「老照片」风格\n"
                          "    指令：表情包做旧 [次数]（建议 1~20）\n"
                          "    别名：yy做旧 / 娅娅做旧 / 娅娅把它做古\n\n"
                          "2️⃣ 多图合成gif：把多张图拼成动图表情包\n"
                          "    指令：多图合成gif [每帧秒数]（如 0.5）\n"
                          "    别名：yy多图合成gif / 娅娅多图合成\n\n"
                          "3️⃣ 精灵图合成：一张大图按网格切成动图\n"
                          "    指令：合成1gif 6x6 0.1 / 合成2gif 6x6 0.1\n"
                          "    别名：yy合成1gif / 娅娅合成1\n\n"
                          "4️⃣ 网格裁剪：把表情包大图切成单张\n"
                          "    指令：裁剪 3x4\n"
                          "    别名：yy裁剪 / 娅娅裁剪\n\n"
                          "回复图片/动图后发指令就行啦～有不会的随时问{nick}♪",

# 黑化彩蛋（_worker_age_meme / 倒放 / heavy 倍速抽帧时偶尔出击）
    "dark_echo_1":       "【{nick}·鸣式】…稍微，黑化一点点也没关系吧？",
}


@register(
    "astrbot_plugin_giftoolbox_dania",
    "Anonymous",
    "达妮娅图片处理：GIF/APNG/WebP 裁剪·合成·分解·变速(倍速&帧率,支持>50fps抽帧)·倒放·表情包做旧·本地转线稿·多图合成，集成《鸣潮》达妮娅(娅娅)风格文案与别名指令",
    "1.0.0",
    "https://github.com/xiaoxi2760/astrbot_plugin_giftoolbox_dania",
)
class SpriteToGifPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.cfg = config if config is not None else {}

        # 达妮娅(娅娅)风格昵称
        danya_name = self.cfg.get('danya_name', '娅娅')
        self.danya_name = danya_name if isinstance(danya_name, str) and danya_name.strip() else '娅娅'

        if imageio is None:
            logger.warning("插件[astrbot_plugin_giftoolbox_dania]检测到缺少 imageio 库。请运行 pip install imageio[ffmpeg]")

    # --- 达妮娅(娅娅)文案中心 ---
    def _danya(self, key: str, **kw) -> str:
        """
        从 DANYA_TEXTS 取风格化文案并渲染占位符。
        模板内可使用 {nick}（自动替换为 self.danya_name）以及其它占位符。
        找不到 key 或渲染异常时，原样返回 key，绝不阻断主流程。
        """
        kw.setdefault('nick', self.danya_name)
        tpl = DANYA_TEXTS.get(key, key)
        try:
            return tpl.format(**kw) if ('{' in tpl) else tpl
        except Exception:
            return key

    async def _emit_text_auto(self, event: AstrMessageEvent, key: str, stop: bool = False, **kw):
        """统一发送纯文本。自动处理 QQ Official 直发与 yield 回灌。作为 async generator 使用：`async for r in ...: yield r`。"""
        text = self._danya(key, **kw)
        sent = await self._emit_text(event, text, stop=stop)
        if not sent:
            yield event.plain_result(text)

    async def _emit_chain_auto(self, event: AstrMessageEvent, components: list, key_hint: str = None, stop: bool = False):
        """
        统一发送组合消息（已构造好的 components 列表）。
        key_hint 仅用于日志，不影响内容。
        作为 async generator 使用。
        """
        sent = await self._emit_chain(event, components, stop=stop)
        if not sent:
            yield event.chain_result(components)

    # --- 平台检测 & 统一回复（QQ Official 绕过 ResultDecorateStage）---
    def _is_qqofficial(self, event: AstrMessageEvent) -> bool:
        """检测当前消息是否来自 QQ Official 平台"""
        try:
            name = event.get_platform_name()
            return name in ("qq_official_full", "qq_official_full_webhook")
        except Exception:
            return False

    async def _emit_text(self, event: AstrMessageEvent, text: str, stop: bool = False) -> bool:
        """
        发送纯文本回复。
        - QQ Official 平台：使用 event.send() 直接发送，绕过 ResultDecorateStage（避免无效 At 组件插入）
        - 其他平台：返回 False，调用方通过 yield event.plain_result() 发送
        返回 True 表示已通过 event.send() 直接发送，调用方不应再 yield。
        """
        if stop:
            event.stop_event()
        if self._is_qqofficial(event):
            chain = MessageChain()
            chain.chain = [Comp.Plain(text)]
            await event.send(chain)
            return True
        return False

    async def _emit_chain(self, event: AstrMessageEvent, components: list, stop: bool = False) -> bool:
        """
        发送组合消息回复（文本 + 图片等）。
        - QQ Official 平台：使用 event.send() 直接发送，绕过 ResultDecorateStage
        - 其他平台：返回 False，调用方通过 yield event.chain_result() 发送
        返回 True 表示已通过 event.send() 直接发送，调用方不应再 yield。
        """
        if stop:
            event.stop_event()
        if self._is_qqofficial(event):
            chain = MessageChain()
            chain.chain = components
            await event.send(chain)
            return True
        return False

    # --- 核心工具：统一保存动画 ---
    def _save_animation(self, output: io.BytesIO, frames: list, duration_ms: int, loop: int = 0):
        fmt = self.cfg.get('output_format', 'GIF').upper()
        if fmt == 'GIF':
            frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=duration_ms,
                           loop=loop, optimize=True, disposal=2)
        elif fmt == 'APNG':
            frames[0].save(output, format='PNG', save_all=True, append_images=frames[1:], duration=duration_ms,
                           loop=loop, optimize=True, default_image=True)
        elif fmt == 'WEBP':
            frames[0].save(output, format='WEBP', save_all=True, append_images=frames[1:], duration=duration_ms,
                           loop=loop, method=3, quality=80)
        else:
            frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=duration_ms,
                           loop=loop, optimize=True, disposal=2)

    # --- 辅助方法: 获取单张图片URL (增强版) ---
    def _get_image_url(self, event: AstrMessageEvent) -> str:
        """获取目标图片URL：优先回复的图片 -> 当前消息的图片 -> At对象的头像"""
        
        # 1. 检查回复链
        if hasattr(event.message_obj, "message"):
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.Reply) and seg.chain:
                    for item in seg.chain:
                        if isinstance(item, Comp.Image) and item.url: 
                            return item.url
                        if isinstance(item, dict) and item.get('type') == 'image':
                            return item.get('data', {}).get('url') or item.get('url') or item.get('file')

        # 2. 检查当前消息中的图片
        # 优先使用 AstrBot 提供的便捷方法
        if hasattr(event, "get_images"):
            images = event.get_images()
            if images: return images[0].url
            
        # 再次手动检查 chain (防止便捷方法遗漏)
        if hasattr(event.message_obj, "message"):
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.Image) and seg.url:
                    return seg.url
                if isinstance(seg, dict) and seg.get('type') == 'image':
                    return seg.get('data', {}).get('url') or seg.get('url') or seg.get('file')

        # 3. 检查 At (获取头像)
        if hasattr(event.message_obj, "message"):
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.At):
                    # 尝试排除机器人自己 (如果能获取到 self_id)
                    # 此处假设用户 At 别人是为了获取头像
                    user_id = str(seg.qq)
                    return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

        return None

    # --- 新增: 递归提取所有图片 (支持合并转发、回复等) ---
    def _extract_images_from_chain(self, chain: list) -> list[str]:
        urls = []
        for item in chain:
            # 1. 直接是 Image 组件
            if isinstance(item, Comp.Image) and item.url:
                urls.append(item.url)
            # 2. 字典格式
            elif isinstance(item, dict):
                if item.get('type') == 'image':
                    url = item.get('data', {}).get('url') or item.get('url') or item.get('file')
                    if url and isinstance(url, str) and url.startswith('http'):
                        urls.append(url)
                # 3. 嵌套节点 (Forward Node)
                elif item.get('type') == 'node':
                    content = item.get('data', {}).get('content') or item.get('content')
                    if isinstance(content, list):
                        urls.extend(self._extract_images_from_chain(content))
            # 4. Reply 组件
            elif isinstance(item, Comp.Reply) and item.chain:
                urls.extend(self._extract_images_from_chain(item.chain))
            # 5. Nodes 组件
            elif isinstance(item, Comp.Nodes):
                if item.nodes:
                    for node in item.nodes:
                        if isinstance(node.content, list):
                            urls.extend(self._extract_images_from_chain(node.content))
        return urls

    async def _get_all_image_urls(self, event: AstrMessageEvent) -> list[str]:
        """获取上下文中所有的图片链接（包括当前消息、回复的消息、转发消息、At头像）"""
        urls = []

        # 1. 检查 event.message_obj.message
        if hasattr(event.message_obj, "message") and isinstance(event.message_obj.message, list):
            urls.extend(self._extract_images_from_chain(event.message_obj.message))

        # 2. 补充 get_images
        if hasattr(event, "get_images"):
            imgs = event.get_images()
            for img in imgs:
                if img.url and img.url not in urls:
                    urls.append(img.url)
        
        # 3. 补充 At 头像
        if hasattr(event.message_obj, "message"):
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.At):
                    uid = str(seg.qq)
                    url = f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640"
                    if url not in urls:
                        urls.append(url)

        # 去重但保持顺序
        seen = set()
        unique_urls = []
        for u in urls:
            if u not in seen:
                unique_urls.append(u)
                seen.add(u)
        return unique_urls

    # --- 辅助方法: 智能获取视频源 ---
    def _get_video_source(self, event: AstrMessageEvent) -> str:
        candidates = []

        def extract_from_item(item):
            url = getattr(item, 'url', None)
            if not url and isinstance(item, dict):
                url = item.get('data', {}).get('url') or item.get('url')
            if url and isinstance(url, str) and url.startswith('http'):
                return 100, url
            path = getattr(item, 'path', None)
            if not path and isinstance(item, dict):
                path = item.get('data', {}).get('path') or item.get('path')
            if path and isinstance(path, str) and os.path.isabs(path) and os.path.exists(path):
                return 90, path
            file_info = getattr(item, 'file', None)
            if not file_info and isinstance(item, dict):
                file_info = item.get('data', {}).get('file') or item.get('file')
            if file_info and isinstance(file_info, str):
                return 50, file_info
            return 0, None

        items_to_check = []
        if hasattr(event, "get_videos"):
            videos = event.get_videos()
            if videos: items_to_check.extend(videos)

        if hasattr(event.message_obj, "message"):
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.Reply) and seg.chain:
                    items_to_check.extend(seg.chain)
                elif isinstance(seg, (Comp.Video, dict)):
                    items_to_check.append(seg)
                elif isinstance(seg, dict) and seg.get('type') == 'video':
                    items_to_check.append(seg)

        for item in items_to_check:
            score, val = extract_from_item(item)
            if val: candidates.append((score, val))

        if not candidates: return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    # --- 通过API解析文件ID ---
    async def _resolve_file_via_api(self, event: AstrMessageEvent, file_id: str) -> str:
        try:
            logger.info(f"尝试通过API解析文件ID: {file_id}")
            res = await event.bot.api.call_action("get_file", file_id=file_id)
            if not res or not isinstance(res, dict): return None
            url = res.get('url')
            if url and url.startswith('http'): return url
            path = res.get('file')
            if path and os.path.exists(path): return path
            return url or path
        except Exception as e:
            logger.warning(f"API解析文件失败: {e}")
            return None

    # --- 智能参数解析 ---
    def _parse_video_args(self, text: str):
        default_scale = self.cfg.get('default_scale', 0.3)
        default_fps = self.cfg.get('default_fps', 10)
        params = {
            'start': 0.0, 'end': None, 'fps': default_fps,
            'step': 1, 'scale': default_scale, 'force_step': False
        }
        time_range = re.search(r'(\d+(?:\.\d+)?)[sS]?\s*[-~]\s*(\d+(?:\.\d+)?)[sS]?', text)
        if time_range:
            params['start'] = float(time_range.group(1))
            params['end'] = float(time_range.group(2))
            text = text.replace(time_range.group(0), " ")
        else:
            start_match = re.search(r'(?:开始|start)\s*(\d+(?:\.\d+)?)', text)
            dur_match = re.search(r'(?:时长|len|time)\s*(\d+(?:\.\d+)?)', text)
            if start_match: params['start'] = float(start_match.group(1))
            if dur_match: params['end'] = params['start'] + float(dur_match.group(1))

        step_match = re.search(r'(\d+)\s*/\s*(\d+)', text)
        if step_match:
            n1 = int(step_match.group(1))
            n2 = int(step_match.group(2))
            step_val = max(n1, n2)
            if step_val > 0:
                params['step'] = step_val
                params['fps'] = None
                params['force_step'] = True
            text = text.replace(step_match.group(0), " ")
        else:
            fps_match = re.search(r'(?:fps|帧率)\s*(\d+)', text)
            if fps_match: params['fps'] = int(fps_match.group(1))

        scale_match = re.search(r'\b(0\.\d+|1\.0)\b', text)
        if scale_match: params['scale'] = float(scale_match.group(1))
        if params['scale'] < 0.1: params['scale'] = 0.1
        if params['scale'] > 1.0: params['scale'] = 1.0
        return params

    # --- 核心处理逻辑 ---
    def _process_gif_core(self, video_path: str, params: dict, max_colors: int = 256):
        try:
            reader = imageio.get_reader(video_path, format='FFMPEG')
            meta = reader.get_meta_data()
            video_duration = meta.get('duration', 100)
            src_fps = meta.get('fps', 30) or 30
            start_t = params['start']
            end_t = params['end'] if params['end'] is not None else video_duration
            max_dur_conf = self.cfg.get('max_gif_duration', 10.0)
            warn_msg = ""
            if (end_t - start_t) > max_dur_conf:
                end_t = start_t + max_dur_conf
                warn_msg = self._danya("video_too_long_warn", s=max_dur_conf)
            end_t = min(end_t, video_duration)
            if start_t >= video_duration: return None, self._danya("video_start_overflow"), 0

            step = 1
            target_fps = 0
            if params.get('force_step'):
                step = params['step']
                target_fps = src_fps / step
            elif params.get('fps'):
                target_fps = params['fps']
                if target_fps > src_fps: target_fps = src_fps
                step = max(1, int(src_fps / target_fps))
            else:
                step = 3
                target_fps = src_fps / step

            frames = []
            output_fmt = self.cfg.get('output_format', 'GIF').upper()
            for i, frame in enumerate(reader):
                current_time = i / src_fps
                if current_time < start_t: continue
                if current_time > end_t: break
                if i % step == 0:
                    pil_img = PILImage.fromarray(frame)
                    w, h = pil_img.size
                    new_w = int(w * params['scale'])
                    new_h = int(h * params['scale'])
                    pil_img = pil_img.resize((new_w, new_h), PILImage.Resampling.BILINEAR)
                    if output_fmt == 'GIF' and max_colors < 256:
                        pil_img = pil_img.quantize(colors=max_colors, method=1, dither=PILImage.Dither.FLOYDSTEINBERG)
                    frames.append(pil_img)
                if len(frames) > 400:
                    warn_msg += " " + self._danya("video_truncate_warn")
                    break
            reader.close()
            if not frames: return None, self._danya("video_no_frames"), 0
            output = io.BytesIO()
            duration_ms = int(1000 / target_fps) if target_fps > 0 else 100
            self._save_animation(output, frames, duration_ms, loop=0)
            output.seek(0)
            size_mb = output.getbuffer().nbytes / 1024 / 1024
            info = f"时间:{start_t}-{end_t:.1f}s {warn_msg}\n格式:{output_fmt} | FPS:{target_fps:.1f}\n缩放:{params['scale']} | 体积:{size_mb:.2f}MB"
            return output, info, size_mb
        except Exception as e:
            return None, self._danya("video_internal_err", err=repr(e)), 0

    def _worker_video_to_gif_wrapper(self, video_path: str, params: dict):
        if imageio is None: return self._danya("video_format_unsupport"), None
        max_colors = self.cfg.get('gif_max_colors', 256)
        gif_io, msg, size_mb = self._process_gif_core(video_path, params, max_colors)
        if not gif_io: return msg, None
        output_fmt = self.cfg.get('output_format', 'GIF').upper()
        if size_mb > 10.0 and output_fmt == 'GIF':
            new_params = params.copy()
            new_msg_prefix = self._danya("video_transmute_big", sz=f"{size_mb:.1f}")
            new_colors = 128 if max_colors > 128 else 64
            new_params['scale'] = round(params['scale'] * 0.8, 2)
            if new_params['scale'] < 0.1: new_params['scale'] = 0.1
            retry_io, retry_msg, retry_size = self._process_gif_core(video_path, new_params, new_colors)
            if retry_io and retry_size < size_mb:
                return new_msg_prefix + retry_msg, retry_io
            else:
                return self._danya("video_compress_fail", sz=f"{retry_size:.1f}") + msg, gif_io
        return self._danya("video_convert_ok") + "\n" + msg, gif_io

    async def _read_local_file(self, path: str) -> bytes:
        """异步读取本地文件（兼容 v4.26.2 PreProcessStage 把 url 替换成本地路径的情况）。"""
        try:
            return await asyncio.to_thread(lambda: open(path, 'rb').read())
        except Exception as e:
            logger.error(f"读取本地文件失败: {path} -> {type(e).__name__}: {e}")
            return None

    async def _download_content(self, url: str) -> bytes:
        if not url.startswith("http"):
            return await self._read_local_file(url)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=60) as resp:
                    if resp.status != 200: return None
                    return await resp.read()
            except Exception as e:
                logger.error(f"_download_content 下载失败: {url} -> {type(e).__name__}: {e}")
                return None

    def _worker_local_line_art(self, img_bytes: bytes) -> bytes:
        """本地线稿生成算法"""
        try:
            # 1. 打开图片
            img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")

            # 2. 转换为灰度
            gray = img.convert("L")

            # 3. 边缘检测 (FIND_EDGES 效果类似素描)
            edges = gray.filter(ImageFilter.FIND_EDGES)

            # 4. 颜色反转 (黑底白线 -> 白底黑线)
            result = ImageOps.invert(edges)

            # 5. 增强对比度 (让线条更清晰)
            enhancer = ImageEnhance.Contrast(result)
            result = enhancer.enhance(3.0)  # 提高对比度

            # 6. 保存
            output = io.BytesIO()
            result.save(output, format='JPEG', quality=90)
            return output.getvalue()
        except Exception as e:
            return None

    # --- 修复增强版: 本地图片转线稿 (无需API) ---
    @filter.command("图片转线稿")
    async def img_to_line_art(self, event: AstrMessageEvent):
        img_url = self._get_image_url(event)
        if not img_url:
            async for r in self._emit_text_auto(event, "need_image", stop=True):
                yield r
            return

        async for r in self._emit_text_auto(event, "lineart_proc"):
            yield r

        # 1. 下载图片 (Bot自己下载，避免API防盗链问题)
        img_bytes = await self._download_content(img_url)
        if not img_bytes:
            async for r in self._emit_text_auto(event, "lineart_dl_fail", stop=True):
                yield r
            return

        # 2. 本地算法处理
        result_bytes = await asyncio.to_thread(self._worker_local_line_art, img_bytes)

        if result_bytes:
            async for r in self._emit_chain_auto(event, [
                Comp.Plain(self._danya("lineart_ok")),
                Comp.Image.fromBytes(result_bytes)
            ], stop=True):
                yield r
        else:
            async for r in self._emit_text_auto(event, "lineart_fail", stop=True):
                yield r

    @filter.command("视频转gif")
    async def video_to_gif_cmd(self, event: AstrMessageEvent):
        if imageio is None:
            async for r in self._emit_text_auto(event, "video_format_unsupport", stop=True):
                yield r
            return
        msg_text = event.message_str.replace("视频转gif", "")
        params = self._parse_video_args(msg_text)
        raw_source = self._get_video_source(event)
        if not raw_source:
            async for r in self._emit_text_auto(event, "need_video", stop=True):
                yield r
            return
        valid_source = None
        if raw_source.startswith("http") or os.path.exists(raw_source):
            valid_source = raw_source
        else:
            async for r in self._emit_text_auto(event, "video_requesting"):
                yield r
            valid_source = await self._resolve_file_via_api(event, raw_source)
            if not valid_source:
                async for r in self._emit_text_auto(event, "video_resolve_fail", src=raw_source, stop=True):
                    yield r
                return
        fmt = self.cfg.get('output_format', 'GIF')
        time_info = f"{params['start']}s-" + (f"{params['end']}s" if params['end'] else "末尾")
        async for r in self._emit_text_auto(event, "video_accepting", fmt=fmt, range=time_info, scale=params['scale']):
            yield r
        tmp_path = ""
        is_temp_file = False
        try:
            if valid_source.startswith("http"):
                max_size = self.cfg.get('max_video_size_mb', 50.0) * 1024 * 1024
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                    is_temp_file = True
                headers = {"User-Agent": "Mozilla/5.0"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(valid_source, headers=headers, timeout=120) as resp:
                        if resp.status != 200:
                            async for r in self._emit_text_auto(event, "video_dl_fail", stop=True):
                                yield r
                            if os.path.exists(tmp_path): os.remove(tmp_path)
                            return
                        content_len = resp.headers.get('Content-Length')
                        if content_len and int(content_len) > max_size:
                            async for r in self._emit_text_auto(event, "video_too_big", stop=True):
                                yield r
                            if os.path.exists(tmp_path): os.remove(tmp_path)
                            return
                        with open(tmp_path, 'wb') as f:
                            f.write(await resp.read())
            else:
                tmp_path = valid_source
                is_temp_file = False
            result_msg, gif_bytes = await asyncio.to_thread(self._worker_video_to_gif_wrapper, tmp_path, params)
            if is_temp_file and os.path.exists(tmp_path): os.remove(tmp_path)
            if gif_bytes:
                async for r in self._emit_chain_auto(event, [Comp.Plain(result_msg), Comp.Image.fromBytes(gif_bytes.getvalue())], stop=True):
                    yield r
            else:
                if not await self._emit_text(event, result_msg, stop=True):
                    yield event.plain_result(result_msg)
        except Exception as e:
            if is_temp_file and tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
            async for r in self._emit_text_auto(event, "proc_err", err=repr(e), stop=True):
                yield r

    # --- 其他功能保持 ---
    def _parse_margins(self, text: str):
        margins = {'top': 0, 'bottom': 0, 'left': 0, 'right': 0}
        pattern = r'边距\s*([上下左右])?边?\s*(\d+)'
        matches = re.findall(pattern, text)
        for direction, amount_str in matches:
            try:
                amount = int(amount_str)
                if not direction:
                    for k in margins: margins[k] += amount
                elif direction == '上':
                    margins['top'] += amount
                elif direction == '下':
                    margins['bottom'] += amount
                elif direction == '左':
                    margins['left'] += amount
                elif direction == '右':
                    margins['right'] += amount
            except ValueError:
                pass
        clean_text = re.sub(pattern, " ", text)
        return clean_text, margins

    def _crop_image_data(self, img_data: bytes, margins: dict) -> tuple[bytes, str]:
        if all(v == 0 for v in margins.values()): return img_data, ""
        try:
            img = PILImage.open(io.BytesIO(img_data)).convert("RGBA")
            w, h = img.size
            l, u, r, d = margins['left'], margins['top'], w - margins['right'], h - margins['bottom']
            if l >= r or u >= d: return img_data, self._danya("make_crop_invalid", w=w, h=h, l=l, u=u, r=r, d=d)
            output = io.BytesIO()
            img.crop((l, u, r, d)).save(output, format='PNG')
            return output.getvalue(), self._danya("make_crop_msg", t=margins['top'], b=margins['bottom'], l=margins['left'], r=margins['right'])
        except Exception as e:
            return img_data, self._danya("make_crop_err", err=e)

    async def _download_image(self, url: str) -> bytes:
        """下载图片/动图。支持 HTTP URL 和本地文件路径。"""
        if not url.startswith("http"):
            return await self._read_local_file(url)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=30) as resp:
                    if resp.status != 200: return None
                    return await resp.read()
            except Exception as e:
                logger.error(f"_download_image 下载失败: {url} -> {type(e).__name__}: {e}")
                return None

    async def _handle_gif_task(self, event: AstrMessageEvent, algorithm_mode: int):
        msg_text = event.message_str
        clean_text, margins = self._parse_margins(msg_text)
        clean_text = clean_text.replace("合成1gif", "").replace("合成2gif", "").replace("合成gif", "")
        rows, cols, duration = 6, 6, 0.1
        grid_match = re.search(r'(\d+)\s*[*x×]\s*(\d+)', clean_text)
        if grid_match:
            rows, cols = int(grid_match.group(1)), int(grid_match.group(2))
            clean_text = clean_text.replace(grid_match.group(0), " ")
        dur_match = re.search(r'(\d+(?:\.\d+)?)', clean_text)
        if dur_match:
            try:
                val = float(dur_match.group(1))
                if 0 < val <= 60: duration = val
            except:
                pass
        img_url = self._get_image_url(event)
        if not img_url:
            async for r in self._emit_text_auto(event, "need_image", stop=True):
                yield r
            return
        async for r in self._emit_text_auto(event, "make_proc_mode", mode=algorithm_mode, r=rows, c=cols, dur=duration):
            yield r
        img_data = await self._download_image(img_url)
        if not img_data:
            async for r in self._emit_text_auto(event, "image_dl_fail", stop=True):
                yield r
            return
        img_data, crop_msg = await asyncio.to_thread(self._crop_image_data, img_data, margins)
        func = self.process_mode_1 if algorithm_mode == 1 else self.process_mode_2
        res_msg, gif_bytes = await asyncio.to_thread(func, img_data, rows, cols, duration)
        if gif_bytes:
            async for r in self._emit_chain_auto(event, [Comp.Plain(res_msg + crop_msg), Comp.Image.fromBytes(gif_bytes.getvalue())], stop=True):
                yield r
        else:
            async for r in self._emit_text_auto(event, "make_fail", info=res_msg, stop=True):
                yield r

    @filter.command("合成1gif")
    async def make_gif_v1(self, event: AstrMessageEvent):
        async for res in self._handle_gif_task(event, 1): yield res

    @filter.command("合成2gif")
    async def make_gif_v2(self, event: AstrMessageEvent):
        async for res in self._handle_gif_task(event, 2): yield res

    def process_mode_1(self, img_data: bytes, rows: int, cols: int, duration_sec: float):
        try:
            img = PILImage.open(io.BytesIO(img_data))
            if getattr(img, "is_animated", False): img.seek(0)
            img = img.convert("RGBA")
            w, h = img.size
            cw, ch = w // cols, h // rows
            if cw < 2 or ch < 2: return self._danya("too_small_grid", w=cw, h=ch), None
            frames = []
            for r in range(rows):
                for c in range(cols):
                    frames.append(img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)))
            output = io.BytesIO()
            self._save_animation(output, frames, int(duration_sec * 1000), loop=0)
            output.seek(0)
            return self._danya("make_ok_1", w=w, h=h, r=rows, c=cols), output
        except Exception as e:
            return self._danya("make_logic_err", err=e), None

    def process_mode_2(self, img_data: bytes, rows: int, cols: int, duration_sec: float):
        try:
            img = PILImage.open(io.BytesIO(img_data))
            if getattr(img, "is_animated", False): img.seek(0)
            img = img.convert("RGBA")
            datas = img.getdata()
            new_data = [(0, 0, 0, 0) if item[3] < 128 else (item[0], item[1], item[2], 255) for item in datas]
            img.putdata(new_data)
            has_trans = any(d[3] == 0 for d in new_data)
            master_pal = img.convert("RGB").quantize(colors=255 if has_trans else 256, method=1)
            w, h = img.size
            cw, ch = w // cols, h // rows
            if cw < 2 or ch < 2: return self._danya("too_small_grid", w=cw, h=ch), None
            frames = []
            for r in range(rows):
                for c in range(cols):
                    crop = img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
                    frame = crop.convert("RGB").quantize(palette=master_pal)
                    if has_trans:
                        mask = crop.split()[3].point(lambda a: 255 if a < 128 else 0)
                        frame.paste(255, mask=mask)
                    frames.append(frame)
            output = io.BytesIO()
            fmt = self.cfg.get('output_format', 'GIF').upper()
            if fmt == 'GIF':
                frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:],
                               duration=int(duration_sec * 1000), loop=0, disposal=2,
                               transparency=255 if has_trans else None, optimize=True)
            else:
                self._save_animation(output, frames, int(duration_sec * 1000), loop=0)
            output.seek(0)
            return self._danya("make_ok_2", w=w, h=h, r=rows, c=cols), output
        except Exception as e:
            return self._danya("make_logic_err", err=e), None

    # --- 统一变速处理逻辑 (v2: 支持倍速/帧率两种模式, fps>50自动抽帧) ---
    async def _handle_speed(self, event: AstrMessageEvent, value: float, is_fps_mode: bool, action_hint: str = "变速"):
        """
        统一的 GIF 变速入口 (async generator)。三个 handler 都改走这里，消除重复模板代码。
        action_hint: '加速' / '减速' / '变速'
        """
        img_url = self._get_image_url(event)
        if not img_url:
            async for r in self._emit_text_auto(event, "need_gif_help", stop=True):
                yield r
            return

        # 进度提示
        if is_fps_mode:
            async for r in self._emit_text_auto(event, "speed_proc_fps", fps=value):
                yield r
        elif action_hint == "加速":
            async for r in self._emit_text_auto(event, "speed_proc_acc", factor=value):
                yield r
        elif action_hint == "减速":
            async for r in self._emit_text_auto(event, "speed_proc_dec", factor=value):
                yield r
        else:
            if value >= 1:
                async for r in self._emit_text_auto(event, "speed_proc_acc", factor=value):
                    yield r
            else:
                async for r in self._emit_text_auto(event, "speed_proc_dec", factor=value):
                    yield r

        img_data = await self._download_image(img_url)
        if not img_data:
            async for r in self._emit_text_auto(event, "video_dl_fail", stop=True):
                yield r
            return

        res_msg, gif_bytes = await asyncio.to_thread(
            self.process_speed_v2, img_data, value, is_fps_mode
        )

        if gif_bytes:
            async for r in self._emit_chain_auto(event, [
                Comp.Plain(res_msg),
                Comp.Image.fromBytes(gif_bytes.getvalue())
            ], stop=True):
                yield r
        else:
            # 失败时 res_msg 已经由 process_speed_v2 用 _danya 渲染好风格短语
            async for r in self._emit_text_auto(event, "speed_fail", info=res_msg, stop=True):
                yield r

    @filter.command("gif变速")
    async def gif_speed_change(self, event: AstrMessageEvent):
        '''GIF 变速: /gif变速 2x (倍速) 或 /gif变速 30fps (帧率)'''
        msg = event.message_str.replace("gif变速", "", 1).strip()

        is_fps_mode = False
        value = 2.0

        # 解析参数: "Nfps" / "N帧" → fps 模式; "Nx" → 倍速模式; 纯数字 → 倍速模式
        fps_match = re.search(r'(\d+\.?\d*)\s*(?:fps|FPS|帧)', msg)
        mult_match = re.search(r'(\d+\.?\d*)\s*[xX×]', msg)
        num_match = re.search(r'(\d+\.?\d*)', msg)

        if fps_match:
            is_fps_mode = True
            value = float(fps_match.group(1))
        elif mult_match:
            value = float(mult_match.group(1))
        elif num_match:
            value = float(num_match.group(1))

        async for r in self._handle_speed(event, value, is_fps_mode, "变速"):
            yield r

    # 保留旧指令作为别名，内部走统一逻辑
    @filter.command("加速")
    @filter.regex(r"^(?:gif)?(?:加速|变快)\s*[*x×]?\s*(\d+\.?\d*)?")
    async def accelerate_gif(self, event: AstrMessageEvent):
        '''GIF加速 (旧指令, 等效于 /gif变速 Nx)'''
        msg = event.message_str
        factor = 2.0
        num_match = re.search(r"(\d+\.?\d*)", msg)
        if num_match:
            factor = float(num_match.group(1))
        async for r in self._handle_speed(event, factor, False, "加速"):
            yield r

    @filter.command("减速")
    @filter.regex(r"^(?:gif)?(?:减速|变慢)\s*[*x×]?\s*(\d+\.?\d*)?")
    async def decelerate_gif(self, event: AstrMessageEvent):
        '''GIF减速 (旧指令, 等效于 /gif变速 Nx)'''
        msg = event.message_str
        factor = 2.0
        num_match = re.search(r"(\d+\.?\d*)", msg)
        if num_match:
            factor = float(num_match.group(1))
        # 减速: 对原速的倒数。让 process_speed_v2 把时长 ×factor。
        async for r in self._handle_speed(event, 1.0 / factor, False, "减速"):
            yield r

    def process_speed_v2(self, img_data: bytes, value: float, is_fps_mode: bool):
        """
        统一变速处理 (v2): 支持倍速模式和帧率模式。
        - 倍速模式: value = 速度倍数 (如 2.0 = 2倍速)
        - 帧率模式: value = 目标 fps (如 30 = 30fps)
        - 当目标 fps > 50 时自动抽帧实现
        返回的 (res_msg, gif_bytes) 中，res_msg 已经是经过 _danya 风格化的最终展示文案。
        """
        MAX_FPS = 50
        MIN_DURATION_MS = 20  # 1000ms / 50fps

        try:
            img = PILImage.open(io.BytesIO(img_data))
            if not getattr(img, "is_animated", False):
                return self._danya("speed_not_anim"), None

            # 提取所有帧及其原始时长
            frames = []
            orig_durations = []
            for frame in ImageSequence.Iterator(img):
                dur = frame.info.get('duration', 100)
                if dur <= 0:
                    dur = 100
                orig_durations.append(dur)
                frames.append(frame.copy())

            if not frames:
                return self._danya("speed_no_frames"), None

            total_duration = sum(orig_durations)
            avg_duration = total_duration / len(frames)
            orig_fps = 1000.0 / avg_duration if avg_duration > 0 else 10.0

            if is_fps_mode:
                target_fps = value

                if target_fps > MAX_FPS:
                    # --- 自动抽帧模式: fps > 50 ---
                    # GIF 最小帧间隔 20ms (=50fps)，无法直接实现 >50fps
                    # 策略: 帧间隔保持 20ms(50fps)，通过抽帧来模拟更高帧率的视觉效果
                    keep_ratio = MAX_FPS / target_fps
                    frames_to_keep = max(2, int(len(frames) * keep_ratio))

                    # 均匀采样保留帧
                    new_frames = []
                    for i in range(frames_to_keep):
                        idx = int(i * len(frames) / frames_to_keep)
                        new_frames.append(frames[idx])

                    new_durations = [MIN_DURATION_MS] * len(new_frames)

                    output = io.BytesIO()
                    new_frames[0].save(
                        output, format='GIF', save_all=True,
                        append_images=new_frames[1:],
                        duration=new_durations, loop=0,
                        disposal=2, optimize=True
                    )
                    output.seek(0)

                    drop_hint = self._danya("speed_done_dropframes", kept=len(new_frames), total=len(frames))
                    msg = (f"{self._danya('speed_done_fps_mode', src=orig_fps, dst=target_fps)}\n"
                           f"💡 {drop_hint}")
                    return msg, output
                else:
                    # 普通帧率模式: 直接设置帧间隔
                    target_duration = int(1000.0 / target_fps)
                    new_durations = [target_duration] * len(frames)

                    output = io.BytesIO()
                    frames[0].save(
                        output, format='GIF', save_all=True,
                        append_images=frames[1:],
                        duration=new_durations, loop=0,
                        disposal=2, optimize=True
                    )
                    output.seek(0)

                    msg = self._danya("speed_done_fps_mode", src=orig_fps, dst=target_fps)
                    return msg, output
            else:
                # 倍速模式: value = 速度倍数
                speed_factor = value
                ratio = 1.0 / speed_factor  # 时长缩放比例

                # 先计算不钳制的目标帧时长，判断是否需要抽帧
                raw_durations = []
                for d in orig_durations:
                    raw_durations.append(int(d * ratio))

                # 最低 1fps: 单帧时长不超过 1000ms
                MAX_RAW_DURATION = 1000
                raw_durations = [min(d, MAX_RAW_DURATION) for d in raw_durations]

                avg_raw_dur = sum(raw_durations) / len(raw_durations)

                # 黑化彩蛋: 高倍速加速(>=4x) 时偶尔出击
                dark = ""
                if speed_factor >= 4.0:
                    dark = "\n" + self._danya("dark_echo_1")

                if avg_raw_dur < MIN_DURATION_MS:
                    # --- 等效 fps > 50，需要抽帧 ---
                    # 策略：帧间隔保持 20ms(50fps)，按比例丢弃帧来达到等效倍速
                    keep_ratio = avg_raw_dur / MIN_DURATION_MS
                    frames_to_keep = max(2, int(len(frames) * keep_ratio))

                    new_frames = []
                    for i in range(frames_to_keep):
                        idx = int(i * len(frames) / frames_to_keep)
                        new_frames.append(frames[idx])

                    new_durations = [MIN_DURATION_MS] * len(new_frames)

                    output = io.BytesIO()
                    new_frames[0].save(
                        output, format='GIF', save_all=True,
                        append_images=new_frames[1:],
                        duration=new_durations, loop=0,
                        disposal=2, optimize=True
                    )
                    output.seek(0)

                    effective_fps = 1000.0 / MIN_DURATION_MS
                    if speed_factor > 1:
                        key = "speed_done_acc"
                    elif speed_factor < 1:
                        key = "speed_done_dec"
                    else:
                        key = "speed_done_plain"
                    drop_hint = self._danya("speed_done_dropframes", kept=len(new_frames), total=len(frames))
                    msg = (f"{self._danya(key, factor=speed_factor, src=orig_fps, dst=effective_fps)}\n"
                           f"💡 {drop_hint}{dark}")
                    return msg, output
                else:
                    # 普通倍速模式: 直接设置帧间隔，不需要抽帧
                    new_durations = raw_durations

                    output = io.BytesIO()
                    frames[0].save(
                        output, format='GIF', save_all=True,
                        append_images=frames[1:],
                        duration=new_durations, loop=0,
                        disposal=2, optimize=True
                    )
                    output.seek(0)

                    effective_fps = 1000.0 / (sum(new_durations) / len(new_durations))
                    if speed_factor > 1:
                        key = "speed_done_acc"
                    elif speed_factor < 1:
                        key = "speed_done_dec"
                    else:
                        key = "speed_done_plain"
                    msg = self._danya(key, factor=speed_factor, src=orig_fps, dst=effective_fps) + dark
                    return msg, output

        except Exception as e:
            return self._danya("speed_speed_err", err=e), None

    def _worker_crop_grid(self, img_data: bytes, margins: dict, rows: int, cols: int):
        img_data, crop_msg = self._crop_image_data(img_data, margins)
        try:
            img = PILImage.open(io.BytesIO(img_data)).convert("RGBA")
            w, h = img.size
            cw, ch = w // cols, h // rows
            if cw < 1 or ch < 1: return self._danya("crop_image_too_small", crop=crop_msg), None
            res_list = []
            for r in range(rows):
                for c in range(cols):
                    out = io.BytesIO()
                    img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)).save(out, format='PNG')
                    res_list.append(out.getvalue())
            return crop_msg, res_list
        except Exception as e:
            return self._danya("crop_fail", err=e), None

    @filter.command("裁剪")
    async def crop_and_forward(self, event: AstrMessageEvent):
        clean, margins = self._parse_margins(event.message_str)
        match = re.search(r'(\d+)\s*[*x×]\s*(\d+)', clean)
        rows, cols = (int(match.group(1)), int(match.group(2))) if match else (1, 1)
        if rows > 20 or cols > 20:
            async for r in self._emit_text_auto(event, "crop_too_many", stop=True):
                yield r
            return
        img_url = self._get_image_url(event)
        if not img_url:
            async for r in self._emit_text_auto(event, "crop_no_image", stop=True):
                yield r
            return
        async for r in self._emit_text_auto(event, "crop_proc"):
            yield r
        img_data = await self._download_image(img_url)
        if not img_data:
            async for r in self._emit_text_auto(event, "video_dl_fail", stop=True):
                yield r
            return
        msg, bytes_list = await asyncio.to_thread(self._worker_crop_grid, img_data, margins, rows, cols)
        if not bytes_list:
            if not await self._emit_text(event, msg, stop=True):
                yield event.plain_result(msg)
            return
        nodes = [Comp.Node(name="裁剪", content=[Comp.Plain(f"结果 {rows}x{cols}{msg}")])]
        for b in bytes_list:
            nodes.append(Comp.Node(name="裁剪", content=[Comp.Image.fromBytes(b)]))
        if not await self._emit_chain(event, [Comp.Nodes(nodes=nodes)], stop=True):
            yield event.chain_result([Comp.Nodes(nodes=nodes)])

    @filter.command("gif分解")
    async def decompose_gif(self, event: AstrMessageEvent):
        img_url = self._get_image_url(event)
        if not img_url:
            async for r in self._emit_text_auto(event, "need_gif", stop=True):
                yield r
            return
        async for r in self._emit_text_auto(event, "decompose_proc"):
            yield r
        img_data = await self._download_image(img_url)
        frames = await asyncio.to_thread(self._worker_decompose, img_data)
        if isinstance(frames, str):
            async for r in self._emit_text_auto(event, "decompose_fail", info=frames, stop=True):
                yield r
            return
        nodes = [Comp.Node(name=self.danya_name, content=[Comp.Plain(self._danya("decompose_frame_label", n=i + 1)), Comp.Image.fromBytes(b)]) for i, b in
                 enumerate(frames)]
        async for r in self._emit_chain_auto(event, [Comp.Nodes(nodes=nodes)], stop=True):
            yield r

    def _worker_decompose(self, img_data: bytes):
        try:
            img = PILImage.open(io.BytesIO(img_data))
            if not getattr(img, "is_animated", False): return self._danya("decompose_not_anim")
            frames = []
            for i, frame in enumerate(ImageSequence.Iterator(img)):
                if i >= 100: break
                out = io.BytesIO()
                frame.copy().convert("RGBA").save(out, format='PNG')
                frames.append(out.getvalue())
            return frames
        except Exception as e:
            return self._danya("decompose_err", err=e)

    # --- GIF倒放: 将动画帧顺序反转实现倒放播放 ---
    @filter.command("gif倒放")
    async def gif_reverse(self, event: AstrMessageEvent):
        async for res in self._handle_reverse_gif(event):
            yield res

    @filter.command("倒放")
    async def gif_reverse_alias(self, event: AstrMessageEvent):
        """倒放别名: 兼容不带gif前缀的指令"""
        async for res in self._handle_reverse_gif(event):
            yield res

    async def _handle_reverse_gif(self, event: AstrMessageEvent):
        img_url = self._get_image_url(event)
        if not img_url:
            async for r in self._emit_text_auto(event, "need_gif", stop=True):
                yield r
            return
        async for r in self._emit_text_auto(event, "reverse_proc"):
            yield r
        img_data = await self._download_image(img_url)
        if not img_data:
            async for r in self._emit_text_auto(event, "video_dl_fail", stop=True):
                yield r
            return
        res_msg, gif_bytes = await asyncio.to_thread(self._worker_reverse_gif, img_data)
        if gif_bytes:
            async for r in self._emit_chain_auto(event, [Comp.Plain(res_msg), Comp.Image.fromBytes(gif_bytes.getvalue())], stop=True):
                yield r
        else:
            async for r in self._emit_text_auto(event, "reverse_fail_info", info=res_msg, stop=True):
                yield r

    def _worker_reverse_gif(self, img_data: bytes):
        """将GIF动画帧顺序反转，实现倒放效果"""
        try:
            img = PILImage.open(io.BytesIO(img_data))
            if not getattr(img, "is_animated", False):
                return self._danya("reverse_not_anim"), None

            frames = []
            durations = []
            for frame in ImageSequence.Iterator(img):
                dur = frame.info.get('duration', 100)
                if dur <= 0:
                    dur = 100
                durations.append(dur)
                frames.append(frame.copy().convert("RGBA"))

            if len(frames) < 2:
                return self._danya("reverse_too_short"), None

            frames.reverse()
            durations.reverse()

            # 黑化彩蛋: 倒放本身就是「时间倒流」意味，配一句夜色短语
            dark = "\n" + self._danya("dark_echo_1")

            # 检测是否包含透明像素
            has_trans = any(f.getchannel("A").getextrema()[0] < 255 for f in frames)
            w, h = frames[0].size

            # 构建共享调色板(最多拼16帧取色)，保证各帧颜色一致
            master = PILImage.new("RGB", (w * min(len(frames), 16), h), (255, 255, 255))
            for i, f in enumerate(frames[:16]):
                master.paste(f.convert("RGB"), (i * w, 0))
            master_pal = master.quantize(colors=255 if has_trans else 256, method=1)

            # 统一量化到共享调色板；有透明时保留索引255作为透明色
            gif_frames = []
            for f in frames:
                pf = f.convert("RGB").quantize(palette=master_pal)
                if has_trans:
                    mask = f.getchannel("A").point(lambda a: 255 if a < 128 else 0)
                    pf.paste(255, mask=mask)
                gif_frames.append(pf)

            output = io.BytesIO()
            save_kwargs = dict(
                format='GIF', save_all=True,
                append_images=gif_frames[1:],
                duration=durations, loop=0,
                disposal=2,
                optimize=not has_trans,
            )
            if has_trans:
                # 透明背景: 透明索引与背景索引都指向255，避免恢复背景时露出其他颜色(如绿色)
                save_kwargs['transparency'] = 255
                save_kwargs['background'] = 255
            gif_frames[0].save(output, **save_kwargs)
            output.seek(0)
            total_ms = sum(durations)
            return self._danya("reverse_done", n=len(frames), t=total_ms / 1000) + dark, output
        except Exception as e:
            return self._danya("reverse_fail", err=e), None

    # --- 新增: 多图合成 GIF 核心处理逻辑 ---
    def _worker_multi_image_gif(self, images_bytes: list[bytes], duration_sec: float):
        try:
            pil_images = []
            max_w, max_h = 0, 0

            # 1. 加载所有图片并计算最大尺寸
            for b in images_bytes:
                try:
                    img = PILImage.open(io.BytesIO(b)).convert("RGBA")
                    # 如果是动态图，取第一帧
                    if getattr(img, "is_animated", False):
                        img.seek(0)
                        img = img.copy()
                    pil_images.append(img)
                    max_w = max(max_w, img.width)
                    max_h = max(max_h, img.height)
                except Exception as e:
                    logger.warning(f"加载图片失败: {e}")

            if not pil_images:
                return "❌ 没有有效的图片", None

            frames = []
            # 2. 统一尺寸：保持比例缩放，居中填充
            for img in pil_images:
                # 创建透明背景（如果合成JPG可以改为白色背景）
                bg = PILImage.new("RGBA", (max_w, max_h), (255, 255, 255, 0))

                # 计算缩放比例
                src_ratio = img.width / img.height
                tgt_ratio = max_w / max_h

                if src_ratio > tgt_ratio:
                    # 按照宽度缩放
                    new_w = max_w
                    new_h = int(max_w / src_ratio)
                else:
                    # 按照高度缩放
                    new_h = max_h
                    new_w = int(max_h * src_ratio)

                # 缩放图片
                img_resized = img.resize((new_w, new_h), PILImage.Resampling.BILINEAR)

                # 居中粘贴
                paste_x = (max_w - new_w) // 2
                paste_y = (max_h - new_h) // 2
                bg.paste(img_resized, (paste_x, paste_y), mask=img_resized if 'A' in img_resized.getbands() else None)

                # 将透明部分处理为白色（对于GIF显示效果更好，或者保留透明）
                # 这里为了通用性，如果输出GIF，Pillow会自动处理透明度。
                # 如果希望背景是白色：
                # final_frame = PILImage.new("RGB", (max_w, max_h), (255, 255, 255))
                # final_frame.paste(bg, mask=bg.split()[3])
                frames.append(bg)

            # 3. 保存动画
            output = io.BytesIO()
            duration_ms = int(duration_sec * 1000)
            self._save_animation(output, frames, duration_ms, loop=0)
            output.seek(0)

            return self._danya("multi_ok", n=len(frames)), output

        except Exception as e:
            return self._danya("multi_fail", err=repr(e)), None

    # --- 新增: 表情包做旧功能 (模拟早期互联网传播效果) ---
    def _worker_age_meme(self, img_data: bytes, times: int) -> tuple[str, bytes]:
        """
        模拟早期互联网图片传播的做旧效果:
        1. 绿色通道增强 (变绿)
        2. 低质量JPEG反复压缩 (马赛克失真)
        3. 模糊处理 (变糊)
        4. 饱和度/对比度调整 (颜色脏化)
        自动检测GIF并逐帧处理后重新合成
        """
        try:
            img = PILImage.open(io.BytesIO(img_data))
            
            # 自动检测是否是动图 (GIF/APNG/WebP动图)
            is_animated = getattr(img, "is_animated", False)
            
            if is_animated:
                # === 处理动图: 分解 -> 逐帧做旧 -> 重新合成 ===
                frames = []
                durations = []
                
                # 获取所有帧
                for frame in ImageSequence.Iterator(img):
                    dur = frame.info.get('duration', 100)
                    if dur <= 0:
                        dur = 100
                    durations.append(dur)
                    # 复制帧并转换为RGB进行做旧处理
                    frame_copy = frame.copy().convert("RGB")
                    aged_frame = self._age_single_frame(frame_copy, times)
                    # 转换回P模式以便GIF保存 (带调色板)
                    frames.append(aged_frame)
                
                if not frames:
                    return self._danya("age_anim_read_err") + "\n" + self._danya("dark_echo_1"), None

                # 将RGB帧转换为调色板模式以生成GIF
                gif_frames = []
                for f in frames:
                    # 量化为256色
                    p_frame = f.convert("P", palette=PILImage.Palette.ADAPTIVE, colors=256)
                    gif_frames.append(p_frame)

                output = io.BytesIO()
                gif_frames[0].save(
                    output,
                    format='GIF',
                    save_all=True,
                    append_images=gif_frames[1:],
                    duration=durations,
                    loop=0,
                    disposal=2,
                    optimize=False
                )
                output.seek(0)
                return self._danya("age_done_gif", n=len(frames), m=times), output.getvalue()
            else:
                # === 静态图处理 ===
                img = img.convert("RGB")
                aged_img = self._age_single_frame(img, times)

                output = io.BytesIO()
                # 最终以中低质量JPEG保存，增加"古早"感
                final_quality = max(30, 70 - times * 3)
                aged_img.save(output, format='JPEG', quality=final_quality)
                return self._danya("age_done_static", n=times, q=final_quality), output.getvalue()

        except Exception as e:
            import traceback
            return self._danya("age_fail", err=repr(e)) + "\n" + traceback.format_exc(), None

    def _age_single_frame(self, img: PILImage.Image, times: int) -> PILImage.Image:
        """对单帧图片进行做旧处理 - 渐进式做旧"""
        import random
        
        # 确保是RGB模式
        if img.mode != "RGB":
            img = img.convert("RGB")
        
        for i in range(times):
            # === 1. 绿色通道偏移 (变绿) - 渐进式，不是每次都加 ===
            # 只在特定轮次进行色彩偏移，让变化更加渐进
            if i % 3 == 0:  # 每3次做一次色彩偏移
                r, g, b = img.split()
                
                # 非常轻微的绿色增强 (每次只加1-2)
                green_boost = random.randint(1, 2)
                red_reduce = random.randint(0, 1)
                blue_reduce = random.randint(0, 1)
                
                # 使用函数工厂避免闭包问题
                def make_add_func(val):
                    return lambda x: min(255, x + val)
                def make_sub_func(val):
                    return lambda x: max(0, x - val)
                
                g = g.point(make_add_func(green_boost))
                if red_reduce > 0:
                    r = r.point(make_sub_func(red_reduce))
                if blue_reduce > 0:
                    b = b.point(make_sub_func(blue_reduce))
                
                img = PILImage.merge("RGB", (r, g, b))
            
            # === 2. JPEG压缩失真 (核心做旧效果) ===
            # 模拟多次保存/转发的压缩损失
            # 质量从70逐渐降到25，变化更平缓
            quality = max(25, 70 - i * 3)
            temp_io = io.BytesIO()
            img.save(temp_io, format='JPEG', quality=quality)
            temp_io.seek(0)
            img = PILImage.open(temp_io).convert("RGB")
            
            # === 3. 轻微模糊 (变糊) - 每3次做一次 ===
            if i % 3 == 0:
                blur_radius = 0.2 + (i // 3) * 0.1  # 非常轻微的模糊
                img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            
            # === 4. 轻微锐化 (模拟过度锐化的"塑料感") - 偶尔做 ===
            if i % 5 == 2:
                img = img.filter(ImageFilter.SHARPEN)
            
            # === 5. 轻微降低饱和度 (颜色变脏) ===
            # 变化更加平缓
            if i % 2 == 0:
                enhancer = ImageEnhance.Color(img)
                saturation = max(0.85, 1.0 - 0.015)  # 每次只降1.5%
                img = enhancer.enhance(saturation)
            
            # === 6. 轻微降低对比度 (变灰暗) ===
            if i % 2 == 1:
                enhancer = ImageEnhance.Contrast(img)
                contrast = max(0.85, 1.0 - 0.01)  # 每次只降1%
                img = enhancer.enhance(contrast)
            
            # === 7. 缩放再放大 (像素化) - 仅在高次数时 ===
            if times >= 15 and i == times // 2:
                w, h = img.size
                if w > 50 and h > 50:
                    small = img.resize((int(w * 0.8), int(h * 0.8)), PILImage.Resampling.BILINEAR)
                    img = small.resize((w, h), PILImage.Resampling.BILINEAR)
        
        return img

    @filter.command("表情包做旧")
    @filter.regex(r"^(?:表情包?)?做旧\s*(\d+)?")
    async def age_meme(self, event: AstrMessageEvent):
        """
        表情包做旧功能，模拟早期互联网图片传播效果
        用法：表情包做旧 [次数]
        示例：表情包做旧 10 (做旧10次，数字越大越绿越糊)
        建议：1-5次轻度做旧，5-10次中度做旧，10-20次重度做旧
        """
        msg_text = event.message_str
        
        # 解析做旧次数
        times = 5  # 默认5次
        num_match = re.search(r'做旧\s*(\d+)', msg_text)
        if num_match:
            times = int(num_match.group(1))
        else:
            # 尝试匹配其他数字
            num_match = re.search(r'(\d+)', msg_text)
            if num_match:
                times = int(num_match.group(1))
        
        # 限制范围
        times = max(1, min(times, 50))  # 1-50次
        
        img_url = self._get_image_url(event)
        if not img_url:
            async for r in self._emit_text_auto(event, "age_no_image", stop=True):
                yield r
            return

        # 根据次数给出提示 (取风格化等级标签)
        if times <= 5:
            level_key = "age_level_light"
        elif times <= 10:
            level_key = "age_level_mid"
        elif times <= 20:
            level_key = "age_level_heavy"
        else:
            level_key = "age_level_extreme"
        level = self._danya(level_key)

        async for r in self._emit_text_auto(event, "age_proc", n=times, level=level):
            yield r

        img_data = await self._download_image(img_url)
        if not img_data:
            async for r in self._emit_text_auto(event, "image_dl_fail", stop=True):
                yield r
            return

        # 自动检测动图类型并处理
        res_msg, result_bytes = await asyncio.to_thread(
            self._worker_age_meme, img_data, times
        )

        if result_bytes:
            async for r in self._emit_chain_auto(event, [
                Comp.Plain(f"{res_msg}\n{self._danya('age_done_hint', level=level)}"),
                Comp.Image.fromBytes(result_bytes)
            ], stop=True):
                yield r
        else:
            if not await self._emit_text(event, res_msg, stop=True):
                yield event.plain_result(res_msg)

    @filter.command("多图合成gif")
    async def multi_img_gif(self, event: AstrMessageEvent):
        """
        多图合成GIF，支持直接发送图片、回复含图消息、转发消息。
        用法：多图合成gif [速度/时长]
        示例：多图合成gif 0.5 (每帧0.5秒)
        """
        # 1. 解析参数 (每帧时长)
        msg_text = event.message_str.replace("多图合成gif", "")
        duration = 0.5  # 默认0.5秒

        # 尝试匹配 fps (例如 10fps) -> 转为 duration
        fps_match = re.search(r'(\d+)\s*(?:fps|帧)', msg_text, re.I)
        if fps_match:
            try:
                fps = float(fps_match.group(1))
                if fps > 0: duration = 1.0 / fps
            except:
                pass
        else:
            # 尝试匹配秒数 (例如 0.2)
            sec_match = re.search(r'(\d+(?:\.\d+)?)', msg_text)
            if sec_match:
                try:
                    val = float(sec_match.group(1))
                    if 0.01 <= val <= 60: duration = val
                except:
                    pass

        async for r in self._emit_text_auto(event, "multi_collecting"):
            yield r

        # 2. 获取所有图片链接
        img_urls = await self._get_all_image_urls(event)

        if not img_urls or len(img_urls) < 1:
            async for r in self._emit_text_auto(event, "multi_need_more", stop=True):
                yield r
            return

        async for r in self._emit_text_auto(event, "multi_dl_proc", n=len(img_urls), dur=f"{duration:.2f}"):
            yield r

        # 3. 并发下载图片
        tasks = [self._download_content(url) for url in img_urls]
        results = await asyncio.gather(*tasks)
        valid_bytes = [b for b in results if b is not None]

        if len(valid_bytes) < 1:  # 允许单张图变成GIF (静止或只有一帧)
            async for r in self._emit_text_auto(event, "multi_dl_fail", stop=True):
                yield r
            return

        # 4. 执行合成
        res_msg, gif_io = await asyncio.to_thread(self._worker_multi_image_gif, valid_bytes, duration)

        if gif_io:
            async for r in self._emit_chain_auto(event, [
                Comp.Plain(self._danya("multi_done", n=len(valid_bytes)) + "\n" + self._danya("multi_canvas_hint") + "\n" + res_msg),
                Comp.Image.fromBytes(gif_io.getvalue())
            ], stop=True):
                yield r
        else:
            if not await self._emit_text(event, res_msg, stop=True):
                yield event.plain_result(res_msg)

    # --- 表情包帮助 ---
    @filter.regex(r"^表情包?帮助\s*$")
    async def expression_help(self, event: AstrMessageEvent):
        """表情包相关指令帮助：做旧 / 多图合成 / 精灵图合成 / 裁剪。"""
        async for r in self._emit_text_auto(event, "help_expression", stop=True):
            yield r


    # --- 达妮娅(娅娅/yy/danya)风格别名分发 ---
    # 用法示例:
    #   yy加速 5 / 娅娅加速 5 / yy冲 5
    #   yy减速 3 / 娅娅减速 3 / yy慢 3
    #   yy变速 2x / 娅娅变速 30fps
    #   yy倒放 / 娅娅倒放 / yy倒着走
    #   yy分解 / 娅娅分解 / 娅娅拆开
    #   yy裁剪 3x4 / 娅娅裁剪 3x4 边距10
    #   yy视频转gif 1s-3s 0.5
    #   yy图片转线稿
    #   yy合成1gif 8x8 0.05
    #   yy合成2gif 8x8 0.05
    #   yy做旧 10 / 娅娅把它做古 10
    #   yy多图合成gif 0.5
    @filter.regex(r"^(?:yy|娅娅|danya)\s*(\S.*)$")
    async def danya_alias_dispatcher(self, event: AstrMessageEvent):
        """统一处理 yy/娅娅/danya 前缀的达妮娅风格别名，路由到对应 handler。"""
        text = event.message_str.strip()
        m = re.match(r"^(?:yy|娅娅|danya)\s*(\S.*)$", text, re.IGNORECASE)
        if not m:
            return
        rest = m.group(1).strip()

        # 按从长到短的关键字匹配命令；命中哪个就走哪个底层 handler。
        # 底层 handler 大多靠正则提取参数 (不依赖指令前缀位置)，"yy" 前缀无害。
        mapping = [
            # 表情帮助
            ("表情包帮助",     self.expression_help),
            ("表情帮助",       self.expression_help),
            # 视频/线稿
            ("视频转gif",     self.video_to_gif_cmd),
            ("图片转线稿",     self.img_to_line_art),
            ("画线稿",        self.img_to_line_art),
            # 合成
            ("多图合成gif",   self.multi_img_gif),
            ("多图合成",       self.multi_img_gif),
            ("表情包做旧",     self.age_meme),
            ("合成1gif",      self.make_gif_v1),
            ("合成1",        self.make_gif_v1),
            ("合成2gif",      self.make_gif_v2),
            ("合成2",        self.make_gif_v2),
            # 分解/倒放
            ("gif分解",       self.decompose_gif),
            ("gif倒放",       self.gif_reverse),
            ("gif变速",       self.gif_speed_change),
            ("变速",          self.gif_speed_change),
            ("做旧",          self.age_meme),
            ("做古",          self.age_meme),
            ("倒放",          self.gif_reverse_alias),
            ("倒着走",        self.gif_reverse_alias),
            ("分解",          self.decompose_gif),
            ("拆开",          self.decompose_gif),
            ("裁剪",          self.crop_and_forward),
            # 变速
            ("加速",          self.accelerate_gif),
            ("冲",            self.accelerate_gif),
            ("减速",          self.decelerate_gif),
            ("慢",            self.decelerate_gif),
            ("变快",          self.accelerate_gif),
            ("变慢",          self.decelerate_gif),
        ]

        fn = None
        for kw, h in mapping:
            if kw in rest:
                fn = h
                break
        if fn is None:
            return

        # 阻止 astrbot 把同一条消息再分给其它正则别名 handler (如底层加速 regex)
        try:
            event.stop_event()
        except Exception:
            pass

        async for r in fn(event):
            yield r
