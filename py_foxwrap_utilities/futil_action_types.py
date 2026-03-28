from dataclasses import dataclass
from typing import List

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_animation




@dataclass
class ExportActionData:
    """Info for one Blender action selected for MTAR/GANI export.

    Fields:
    - action: Blender action object
    - frame_start/frame_end: export frame range
    - source: debug string, e.g. "NLA strip '...'"
    - export_clean_threshold: fcurve cleanup threshold used during animation baking
    """
    action: bpy.types.Action
    frame_start: int
    frame_end: int
    source: str
    export_clean_threshold: float = 0.0

    def to_string(self) -> str:
        frame_count = self.frame_end - self.frame_start + 1
        return f"'Action '{self.action.name}' (frames {self.frame_start}-{self.frame_end}, {frame_count} frames) - {self.source}"

    @staticmethod
    def collect_export_action_data_from_armature(
        armature: bpy.types.Object,
        use_nla: bool,
        export_clean_threshold: float = 0.0,
    ) -> List['ExportActionData']:
        """Collect animation actions from *armature* for export.

        This is the shared implementation used by all three track types (motion
        points, shader nodes, and — via wrappers — the main animation tracks).

        Args:
            armature:               Armature object to collect actions from.
            use_nla:                If ``True``, collect from unmuted NLA strips;
                                    if ``False``, use the active action.
            export_clean_threshold: FCurve cleaning threshold (0 = disabled).
            Allows to treat specific actions differently (e.g. motion points or shader params)

        Returns:
            List of :class:`ExportActionData` objects (may be empty).
        """
        if not armature:
            return []

        Debug.log(f"\nCollecting actions from '{armature.name}'...")

        actions: List[ExportActionData] = []

        # NLA
        if (use_nla
            and armature.animation_data
            and armature.animation_data.nla_tracks
        ):
            Debug.log("  Using NLA strips.")
            for nla_track in armature.animation_data.nla_tracks:
                
                # Skip muted tracks
                if nla_track.mute:
                    continue

                for nla_strip in nla_track.strips:
                    
                    # Skip not relevant strips
                    if not util_blender_animation.is_relevant_strip(nla_strip):
                        Debug.log(
                            f"    Skipping {armature.name} strip "
                            f"'{getattr(nla_strip, 'name', '<unknown>')}' "
                            f"(not a GANI strip)"
                        )
                        continue

                    action_data = ExportActionData(
                        action=nla_strip.action,
                        frame_start=int(nla_strip.frame_start),
                        frame_end=int(nla_strip.frame_end),
                        source=f"NLA strip '{nla_strip.name}' on track '{nla_track.name}'",
                        export_clean_threshold=export_clean_threshold,
                    )
                    actions.append(action_data)
                    Debug.log(f"    {action_data.to_string()}")

        # Single action
        elif armature.animation_data and armature.animation_data.action:
            Debug.log("  Using active action.")
            action = armature.animation_data.action
            frame_start = 0
            frame_end = 0

            if util_blender_animation.action_has_fcurves(action):
                frame_start = int(min(kp.co.x for fc in util_blender_animation.iter_action_fcurves(action) for kp in fc.keyframe_points))
                frame_end = int(max(kp.co.x for fc in util_blender_animation.iter_action_fcurves(action) for kp in fc.keyframe_points))

            action_data = ExportActionData(
                action=action,
                frame_start=frame_start,
                frame_end=frame_end,
                source="Active action",
                export_clean_threshold=export_clean_threshold,
            )
            actions.append(action_data)
            Debug.log(f"    {action_data.to_string()}")

        else:
            Debug.log("  No actions found.")

        return actions