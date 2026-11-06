"""ROI rules: whether a pair of rectangles is usable."""

from dataclasses import dataclass

PLANT_IDS = (1, 2)


@dataclass(frozen=True)
class Roi:
    x: int
    y: int
    w: int
    h: int

    def __str__(self) -> str:
        return f"({self.x}, {self.y}, {self.w}, {self.h})"

    def overlaps(self, other: "Roi") -> bool:
        return (
            self.x < other.x + other.w
            and other.x < self.x + self.w
            and self.y < other.y + other.h
            and other.y < self.y + self.h
        )

    def fits_in(self, frame_width: int, frame_height: int) -> bool:
        return self.x >= 0 and self.y >= 0 and self.x + self.w <= frame_width and self.y + self.h <= frame_height


def validate_plants(plants: dict[int, Roi]) -> str | None:
    """Return why this pair of ROIs cannot be used, in the language of the interface."""
    for plant_id, roi in plants.items():
        if roi.w <= 0 or roi.h <= 0:
            return f"Desenhe o ROI da Planta {plant_id}."

        if roi.x < 0 or roi.y < 0:
            return f"O ROI da Planta {plant_id} começa fora do quadro."

    if plants[1].overlaps(plants[2]):
        return "Os ROIs das duas plantas não podem se sobrepor."

    return None
