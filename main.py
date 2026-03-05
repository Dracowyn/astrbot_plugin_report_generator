import asyncio
import re
import unicodedata
import uuid
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageDraw as PILImageDraw
from PIL import ImageFont as PILImageFont

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star
from astrbot.core import pip_installer
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


class Main(Star):
    # 文字区域最大宽度占图片宽度的比例
    _MAX_WIDTH_RATIO: float = 0.80
    # 行间距占字体大小的比例
    _LINE_SPACING_RATIO: float = 0.3
    # 文字描边宽度（像素）
    _STROKE_WIDTH: int = 3
    # 用户输入的最大字符数，防止超长文本耗尽 CPU 资源
    _MAX_INPUT_LENGTH: int = 500
    # 临时目录中本插件最多保留的图片数量
    _MAX_TEMP_FILES: int = 20

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._plugin_dir = Path(__file__).parent
        self._temp_dir = Path(get_astrbot_temp_path())
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        # 持有后台任务的强引用，防止 GC 在任务执行中意外回收
        self._bg_tasks: set[asyncio.Task] = set()

        # 将 pilmoji 类存入实例属性，避免修改模块级全局状态造成竞争隐患
        try:
            from pilmoji import Pilmoji as _PilmojiClass

            self._pilmoji_class: type | None = _PilmojiClass
        except ImportError:
            self._pilmoji_class = None
            # pilmoji 是可选依赖，import 不会失败，因此不会触发 AstrBot 的自动安装机制
            # 需要在此处主动安装，安装完成后重新导入以启用 Emoji 渲染
            logger.info("[report_generator] pilmoji 未安装，正在自动安装...")
            task = asyncio.create_task(
                self._install_pilmoji(),
                name="report_generator_install_pilmoji",
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

    async def _install_pilmoji(self) -> None:
        """后台安装 pilmoji 并重新导入，使本次运行即可启用 Emoji 渲染。"""
        try:
            await pip_installer.install(
                requirements_path=str(self._plugin_dir / "requirements.txt")
            )
            from pilmoji import Pilmoji as _PilmojiImported

            self._pilmoji_class = _PilmojiImported
            logger.info("[report_generator] pilmoji 安装成功，Emoji 渲染已启用。")
        except Exception as e:
            logger.warning(
                f"[report_generator] pilmoji 安装失败，Emoji 将显示为方块: {e}"
            )

    # ------------------------------------------------------------------ #
    #  辅助方法                                                             #
    # ------------------------------------------------------------------ #

    def _get_font_size(self) -> int:
        size = self.config.get("report_font_size", 65)
        try:
            size = int(size)
        except (TypeError, ValueError):
            return 65
        return size if size > 0 else 65

    def _check_access(self, event: AstrMessageEvent) -> tuple[bool, str]:
        """检查访问权限，返回 (是否允许, 拒绝原因)。

        群组过滤仅对群消息生效，私聊消息总是通过。
        """
        group_id = event.get_group_id()
        sender_id = event.get_sender_id()

        # --- 群组过滤 ----------------------------------------------- #
        if group_id and self.config.get("group_filter_enabled", False):
            mode = self.config.get("group_filter_mode", "blacklist")
            group_list = [str(g) for g in self.config.get("group_list", [])]
            if mode == "blacklist" and str(group_id) in group_list:
                return False, "该群组已被禁止使用此功能。"
            if mode == "whitelist" and str(group_id) not in group_list:
                return False, "该群组未授权使用此功能。"

        # --- 用户过滤 ------------------------------------------------ #
        if self.config.get("user_filter_enabled", False):
            allowed = [str(u) for u in self.config.get("allowed_user_ids", [])]
            if allowed:
                # 列表非空时，仅允许列表内的用户
                if str(sender_id) not in allowed:
                    return False, "你没有权限使用此功能。"
            else:
                # 列表为空时，默认仅允许管理员使用
                if not event.is_admin():
                    return False, "你没有权限使用此功能。"

        return True, ""

    @staticmethod
    def _estimate_char_width(char: str, font) -> float:
        """估算单个字符的像素宽度。

        对于 Emoji 或 simhei.ttf 中缺失的字型，PIL 返回 0 或极小值，
        此时回退到 font.size（约等于一个全角字符宽度）以保证换行正常。
        """
        try:
            w = font.getlength(char)
            if w > font.size * 0.1:
                return w
        except (AttributeError, ValueError, OSError):
            pass
        # 字体中不存在该字型（如 Emoji）— 按全角/半角估算宽度
        eaw = unicodedata.east_asian_width(char)
        return font.size if eaw in ("W", "F") else font.size * 0.6

    def _wrap_text(self, msg: str, font, max_width: float) -> str:
        """对 msg 按像素宽度自动换行，使每行不超过 max_width 像素。"""
        result_lines: list[str] = []
        for paragraph in msg.split("\n"):
            current_line = ""
            current_w = 0.0
            for char in paragraph:
                char_w = self._estimate_char_width(char, font)
                if current_w + char_w > max_width and current_line:
                    result_lines.append(current_line)
                    current_line = char
                    current_w = char_w
                else:
                    current_line += char
                    current_w += char_w
            result_lines.append(current_line)
        return "\n".join(result_lines)

    def _generate_report(
        self,
        bg_path: Path,
        msg: str,
        fill_color: tuple,
        stroke_color: tuple,
        out_path: Path,
    ) -> None:
        """将 msg 居中绘制到背景图 bg_path 上，结果保存至 out_path。"""
        font_size = self._get_font_size()
        img = PILImage.open(bg_path).convert("RGBA")
        font = PILImageFont.truetype(
            str(self._plugin_dir / "simhei.ttf"), font_size
        )

        max_width = img.width * self._MAX_WIDTH_RATIO
        wrapped = self._wrap_text(msg, font, max_width)
        lines = wrapped.split("\n")

        # 用 PIL 测量每行尺寸（Emoji 的度量值为近似值，但对居中计算已足够准确）
        dummy_draw = PILImageDraw.Draw(PILImage.new("RGBA", (1, 1)))
        line_spacing = int(font_size * self._LINE_SPACING_RATIO)
        line_metrics: list[tuple[float, float]] = []
        for line in lines:
            measure_text = line if line.strip() else " "
            bbox = dummy_draw.textbbox(
                (0, 0), measure_text, font=font, stroke_width=self._STROKE_WIDTH
            )
            line_metrics.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))

        total_h = sum(h for _, h in line_metrics) + line_spacing * max(
            len(lines) - 1, 0
        )
        start_y = (img.height - total_h) / 2

        if self._pilmoji_class is not None:
            # pilmoji 将 Emoji 码点替换为 Twemoji PNG 图像渲染，避免出现方块乱码
            with self._pilmoji_class(img) as ctx:
                self._draw_lines(
                    ctx,
                    lines,
                    line_metrics,
                    img.width,
                    start_y,
                    line_spacing,
                    font,
                    fill_color,
                    stroke_color,
                )
        else:
            # 降级回原生 PIL — Emoji 将显示为空方块
            ctx = PILImageDraw.Draw(img)
            self._draw_lines(
                ctx,
                lines,
                line_metrics,
                img.width,
                start_y,
                line_spacing,
                font,
                fill_color,
                stroke_color,
            )

        img.convert("RGB").save(str(out_path), "JPEG")

    def _draw_lines(
        self,
        ctx,
        lines: list[str],
        line_metrics: list[tuple[float, float]],
        img_width: int,
        start_y: float,
        line_spacing: int,
        font,
        fill_color: tuple,
        stroke_color: tuple,
    ) -> None:
        """将各行文字通过绘图上下文 ctx 居中绘制到画布上。"""
        current_y = start_y
        for (lw, lh), line in zip(line_metrics, lines):
            x = (img_width - lw) / 2
            ctx.text(
                (int(x), int(current_y)),
                line,
                font=font,
                fill=fill_color,
                stroke_width=self._STROKE_WIDTH,
                stroke_fill=stroke_color,
            )
            current_y += lh + line_spacing

    # ------------------------------------------------------------------ #
    #  指令处理                                                             #
    # ------------------------------------------------------------------ #

    @filter.command("喜报")
    async def congrats(self, event: AstrMessageEvent):
        """喜报生成器。用法：/喜报 <内容>"""
        allowed, reason = self._check_access(event)
        if not allowed:
            return MessageEventResult().message(reason)

        # 用正则去除命令词（含可能的指令前缀如 /），兼容 /喜报 和 喜报 两种触发方式
        msg = re.sub(r"^\S*喜报\s*", "", event.message_str).strip()
        if not msg:
            return MessageEventResult().message("用法：/喜报 <内容>")
        if len(msg) > self._MAX_INPUT_LENGTH:
            return MessageEventResult().message(
                f"输入内容过长（最大 {self._MAX_INPUT_LENGTH} 个字符）。"
            )

        # 使用 UUID 生成唯一文件名，避免并发请求互相覆盖
        # 注意：不能在 finally 中删除文件，平台适配器（如 QQ Official）会在 handler
        # 返回后异步读取文件内容，提前删除会导致 FileNotFoundError
        out_path = self._temp_dir / f"report_congrats_{uuid.uuid4().hex}.jpg"
        self._generate_report(
            self._plugin_dir / "congrats.jpg",
            msg,
            fill_color=(255, 0, 0),
            stroke_color=(255, 255, 0),
            out_path=out_path,
        )
        self._cleanup_old_temp_files()
        return MessageEventResult().file_image(str(out_path))

    @filter.command("悲报")
    async def uncongrats(self, event: AstrMessageEvent):
        """悲报生成器。用法：/悲报 <内容>"""
        allowed, reason = self._check_access(event)
        if not allowed:
            return MessageEventResult().message(reason)

        msg = re.sub(r"^\S*悲报\s*", "", event.message_str).strip()
        if not msg:
            return MessageEventResult().message("用法：/悲报 <内容>")
        if len(msg) > self._MAX_INPUT_LENGTH:
            return MessageEventResult().message(
                f"输入内容过长（最大 {self._MAX_INPUT_LENGTH} 个字符）。"
            )

        # 使用 UUID 生成唯一文件名，避免并发请求互相覆盖
        # 注意：不能在 finally 中删除文件，平台适配器（如 QQ Official）会在 handler
        # 返回后异步读取文件内容，提前删除会导致 FileNotFoundError
        out_path = self._temp_dir / f"report_uncongrats_{uuid.uuid4().hex}.jpg"
        self._generate_report(
            self._plugin_dir / "uncongrats.jpg",
            msg,
            fill_color=(0, 0, 0),
            stroke_color=(255, 255, 255),
            out_path=out_path,
        )
        self._cleanup_old_temp_files()
        return MessageEventResult().file_image(str(out_path))

    def _cleanup_old_temp_files(self) -> None:
        """清理临时目录中本插件生成的旧图片，最多保留最新的 _MAX_TEMP_FILES 个文件。"""
        try:
            files = [
                p
                for p in self._temp_dir.iterdir()
                if p.suffix == ".jpg"
                and p.name.startswith(("report_congrats_", "report_uncongrats_"))
            ]
            if len(files) <= self._MAX_TEMP_FILES:
                return
            # 按修改时间升序排列，删除最旧的超出部分
            files.sort(key=lambda p: p.stat().st_mtime)
            for old_file in files[: len(files) - self._MAX_TEMP_FILES]:
                try:
                    old_file.unlink()
                except OSError as e:
                    logger.warning(
                        f"[report_generator] 清理临时文件失败: {old_file.name}: {e}"
                    )
        except OSError as e:
            logger.warning(f"[report_generator] 扫描临时目录失败: {e}")
