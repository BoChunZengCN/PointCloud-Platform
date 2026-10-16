"""点云到网格第一里程碑的稳定领域错误。"""

from pc_system.model_matching_errors import ModelMatchingError


class ScanToMeshError(ModelMatchingError):
    """向 API、CLI 和页面暴露稳定错误码。"""
