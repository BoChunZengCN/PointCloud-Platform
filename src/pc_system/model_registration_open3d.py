import importlib
import re

import numpy as np

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_registration_engine import EngineDescription, RegistrationEngine


_VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")


def _unavailable(message: str) -> ModelMatchingError:
    return ModelMatchingError("registration_engine_unavailable", message)


def _failed(message: str) -> ModelMatchingError:
    return ModelMatchingError("registration_engine_failed", message)


def _import_open3d():
    try:
        return importlib.import_module("open3d")
    except (ImportError, OSError):
        return None


def _supported_version(value: object) -> bool:
    if type(value) is not str:
        return False
    match = _VERSION.match(value)
    if match is None:
        return False
    major, minor = (int(match.group(index)) for index in (1, 2))
    return major == 0 and minor >= 19


def _matrix(value: object) -> np.ndarray:
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise _failed("Open3D registration matrix is invalid.") from exc
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise _failed("Open3D registration matrix is invalid.")
    return matrix


def _score(fitness: float, rmse: float, maximum_distance: float) -> float:
    value = float(fitness) - float(rmse) / max(float(maximum_distance), 1e-12)
    return round(value, 12)


class Open3DRegistrationEngine:
    def __init__(self, open3d_module):
        self._o3d = open3d_module

    def describe(self) -> EngineDescription:
        return EngineDescription("open3d", self._o3d.__version__, True)

    def _cloud(self, points: object):
        values = np.asarray(points, dtype=np.float64)
        if (
            values.ndim != 2
            or values.shape[1:] != (3,)
            or len(values) < 3
            or not np.isfinite(values).all()
        ):
            raise _failed("Open3D point cloud input is invalid.")
        cloud = self._o3d.geometry.PointCloud()
        cloud.points = self._o3d.utility.Vector3dVector(values)
        return cloud

    def _with_normals(self, cloud, voxel_size: float, config: dict):
        if not cloud.has_points() or len(cloud.points) < 3:
            raise _failed("Open3D downsampled point cloud is insufficient.")
        radius = voxel_size * config["preprocessing"]["normal_radius_multiplier"]
        cloud.estimate_normals(
            search_param=self._o3d.geometry.KDTreeSearchParamHybrid(
                radius=radius,
                max_nn=config["preprocessing"]["normal_max_nn"],
            )
        )
        if not cloud.has_normals() or len(cloud.normals) != len(cloud.points):
            raise _failed("Open3D normal estimation failed.")
        return cloud

    def _downsample(self, cloud, voxel_size: float, config: dict):
        return self._with_normals(cloud.voxel_down_sample(voxel_size), voxel_size, config)

    def _coarse_source(
        self,
        prepared: dict,
        initial: np.ndarray,
        config: dict,
        voxel_size: float,
    ):
        source = self._cloud(np.asarray(prepared["source_down"].points))
        source.transform(initial)
        return self._with_normals(source, voxel_size, config)

    def preprocess(
        self,
        model_points: np.ndarray,
        object_points: np.ndarray,
        config: dict,
    ) -> dict:
        try:
            source = self._cloud(model_points)
            target = self._cloud(object_points)
            voxel = float(config["preprocessing"]["voxel_sizes_m"][0])
            source_down = self._downsample(source, voxel, config)
            target_down = self._downsample(target, voxel, config)
            fpfh_radius = (
                voxel * config["preprocessing"]["fpfh_radius_multiplier"]
            )
            search = self._o3d.geometry.KDTreeSearchParamHybrid(
                radius=fpfh_radius,
                max_nn=config["preprocessing"]["fpfh_max_nn"],
            )
            source_fpfh = (
                self._o3d.pipelines.registration.compute_fpfh_feature(
                    source_down, search
                )
            )
            target_fpfh = (
                self._o3d.pipelines.registration.compute_fpfh_feature(
                    target_down, search
                )
            )
            return {
                "source": source,
                "target": target,
                "source_down": source_down,
                "target_down": target_down,
                "source_fpfh": source_fpfh,
                "target_fpfh": target_fpfh,
                "coarse_voxel_size_m": voxel,
            }
        except ModelMatchingError:
            raise
        except Exception as exc:
            raise _failed("Open3D preprocessing failed.") from exc

    def _coarse_result(
        self,
        result,
        hypothesis: dict,
        initial: np.ndarray,
        maximum_distance: float,
        method: str,
    ) -> dict:
        matrix = np.asarray(result.transformation, dtype=np.float64) @ initial
        fitness = float(result.fitness)
        rmse = float(result.inlier_rmse)
        return {
            "hypothesis_id": hypothesis["hypothesis_id"],
            "source": hypothesis["source"],
            "method": method,
            "matrix": matrix.tolist(),
            "score": _score(fitness, rmse, maximum_distance),
            "coarse_metrics": {"fitness": fitness, "rmse_m": rmse},
        }

    def coarse_register(
        self,
        prepared: dict,
        hypotheses: list[dict],
        config: dict,
    ) -> list[dict]:
        try:
            registration = self._o3d.pipelines.registration
            policy = config["coarse_registration"]
            maximum_distance = (
                prepared["coarse_voxel_size_m"] * policy["distance_multiplier"]
            )
            if hasattr(self._o3d.utility, "random"):
                self._o3d.utility.random.seed(policy["random_seed"])
            checkers = [
                registration.CorrespondenceCheckerBasedOnEdgeLength(
                    policy["edge_length_ratio"]
                ),
                registration.CorrespondenceCheckerBasedOnDistance(
                    maximum_distance
                ),
                registration.CorrespondenceCheckerBasedOnNormal(
                    policy["normal_angle_rad"]
                ),
            ]
            criteria = registration.RANSACConvergenceCriteria(
                policy["maximum_iterations"], policy["confidence"]
            )
            results = []
            for hypothesis in hypotheses:
                initial = _matrix(hypothesis["matrix"])
                source = self._coarse_source(
                    prepared,
                    initial,
                    config,
                    prepared["coarse_voxel_size_m"],
                )
                result = registration.registration_ransac_based_on_feature_matching(
                    source,
                    prepared["target_down"],
                    prepared["source_fpfh"],
                    prepared["target_fpfh"],
                    True,
                    maximum_distance,
                    registration.TransformationEstimationPointToPoint(False),
                    policy["ransac_n"],
                    checkers,
                    criteria,
                )
                results.append(
                    self._coarse_result(
                        result, hypothesis, initial, maximum_distance, "ransac"
                    )
                )
            if policy["fgr_enabled"]:
                fgr = registration.registration_fgr_based_on_feature_matching(
                    prepared["source_down"],
                    prepared["target_down"],
                    prepared["source_fpfh"],
                    prepared["target_fpfh"],
                    registration.FastGlobalRegistrationOption(
                        maximum_correspondence_distance=maximum_distance
                    ),
                )
                identity = {
                    "hypothesis_id": "fgr-global",
                    "source": "fgr",
                }
                results.append(
                    self._coarse_result(
                        fgr, identity, np.eye(4), maximum_distance, "fgr"
                    )
                )
            results.sort(
                key=lambda item: (-item["score"], item["hypothesis_id"], item["method"])
            )
            return results[: policy["top_n"]]
        except ModelMatchingError:
            raise
        except Exception as exc:
            raise _failed("Open3D coarse registration failed.") from exc

    def fine_register(
        self,
        prepared: dict,
        coarse_results: list[dict],
        config: dict,
    ) -> list[dict]:
        try:
            registration = self._o3d.pipelines.registration
            policy = config["fine_registration"]
            results = []
            for coarse in coarse_results:
                transform = _matrix(coarse["matrix"])
                level_metrics = []
                final = None
                for level in policy["levels"]:
                    voxel = float(level["voxel_size_m"])
                    source = self._downsample(prepared["source"], voxel, config)
                    target = self._downsample(prepared["target"], voxel, config)
                    criteria = registration.ICPConvergenceCriteria(
                        relative_fitness=policy["relative_fitness"],
                        relative_rmse=policy["relative_rmse"],
                        max_iteration=level["maximum_iterations"],
                    )
                    final = registration.registration_icp(
                        source,
                        target,
                        level["max_correspondence_distance_m"],
                        transform,
                        registration.TransformationEstimationPointToPlane(),
                        criteria,
                    )
                    transform = np.asarray(final.transformation, dtype=np.float64)
                    level_metrics.append(
                        {
                            "voxel_size_m": voxel,
                            "fitness": float(final.fitness),
                            "rmse_m": float(final.inlier_rmse),
                            "point_counts": {
                                "model": len(source.points),
                                "object": len(target.points),
                            },
                        }
                    )
                if final is None:
                    continue
                maximum_distance = policy["levels"][-1][
                    "max_correspondence_distance_m"
                ]
                results.append(
                    {
                        **coarse,
                        "matrix": transform.tolist(),
                        "score": _score(
                            final.fitness, final.inlier_rmse, maximum_distance
                        ),
                        "fine_metrics": {
                            "fitness": float(final.fitness),
                            "rmse_m": float(final.inlier_rmse),
                            "levels": level_metrics,
                        },
                        "symmetry_equivalent": coarse.get("source")
                        == "declared_symmetry",
                    }
                )
            results.sort(key=lambda item: (-item["score"], item["hypothesis_id"]))
            return results
        except ModelMatchingError:
            raise
        except Exception as exc:
            raise _failed("Open3D fine registration failed.") from exc

    def nearest_neighbor_evidence(
        self,
        prepared: dict,
        transform: np.ndarray,
        config: dict,
    ) -> dict:
        del config
        try:
            source = self._cloud(np.asarray(prepared["source"].points))
            source.transform(_matrix(transform))
            target = prepared["target"]
            return {
                "observed_to_model_distances_m": list(
                    target.compute_point_cloud_distance(source)
                ),
                "model_to_observed_distances_m": list(
                    source.compute_point_cloud_distance(target)
                ),
                "normal_cosines": None,
            }
        except ModelMatchingError:
            raise
        except Exception as exc:
            raise _failed("Open3D nearest-neighbor evaluation failed.") from exc


def load_open3d_registration_engine() -> RegistrationEngine:
    module = _import_open3d()
    if module is None:
        raise _unavailable("Open3D registration dependency is not installed.")
    if not _supported_version(getattr(module, "__version__", None)):
        raise _unavailable("Open3D registration dependency version is incompatible.")
    return Open3DRegistrationEngine(module)


def resolve_registration_engine(name: str) -> RegistrationEngine:
    if name != "open3d":
        raise _unavailable("Requested registration engine is unavailable.")
    return load_open3d_registration_engine()
