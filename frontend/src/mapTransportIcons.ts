import atlasUrl from "./assets/transport-3d-atlas.png";
import type { Mode } from "./networkApi";

// Six transparent 3D illustrations in one local 3 × 2 sprite sheet.
// Vehicle noses point down in the source sheet; map rotations subtract 180°.
const positions: Record<Mode, string> = {
  road: "0% 0%", rail: "50% 0%", air: "100% 0%",
  sea: "0% 100%", river: "50% 100%", pipeline: "100% 100%",
};
export function transportMarkup(mode: Mode, size = 64) {
  return `<span class="route-transport-3d" aria-hidden="true" style="width:${size}px;height:${size}px;background-image:url('${atlasUrl}');background-position:${positions[mode]}"></span>`;
}
