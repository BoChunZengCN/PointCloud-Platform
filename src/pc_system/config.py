from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectConfig:
    """项目级目录配置。

    所有阶段都通过这一个对象获取目录位置，避免每个模块自己拼路径。
    """

    project_root: Path

    def paths(self) -> dict[str, Path]:
        """返回阶段一约定的标准目录，不立即创建目录。"""

        root = self.project_root
        return {
            # 原始输入文件放在 raw；阶段一不会改写用户原始点云。
            "data_raw": root / "data" / "raw",
            # 每个点云资产一个子目录，用来保存 asset.json 等派生信息。
            "assets": root / "data" / "assets",
            "reports": root / "reports",
            "previews": root / "previews",
            "logs": root / "logs",
        }

    def ensure_directories(self) -> dict[str, Path]:
        """创建标准目录，并返回目录映射给调用方继续使用。"""

        paths = self.paths()
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        return paths
