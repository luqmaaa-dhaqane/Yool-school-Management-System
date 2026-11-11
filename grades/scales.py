"""Turning a percentage into a grade label. Schools can differ, so scales are interchangeable objects."""
from abc import ABC, abstractmethod


class GradingScale(ABC):
    @abstractmethod
    def label(self, percent):
        """The grade shown for a percentage (for example 'B')."""

    def remark(self, percent):
        return ""


class PercentScale(GradingScale):
    """No letters: the grade is the percentage itself."""

    def label(self, percent):
        return "" if percent is None else f"{percent:.0f}%"


class BandScale(GradingScale):
    """Letter grades from GradeBand rows (A from 90, B from 80, ...)."""

    def __init__(self, bands):
        self.bands = sorted(bands, key=lambda b: b.min_percent, reverse=True)

    def _band(self, percent):
        for band in self.bands:
            if percent >= band.min_percent:
                return band
        return self.bands[-1] if self.bands else None

    def label(self, percent):
        if percent is None:
            return ""
        band = self._band(percent)
        return band.label if band else ""

    def remark(self, percent):
        if percent is None:
            return ""
        band = self._band(percent)
        return band.remark if band else ""


def scale_for(school):
    from .models import GradeBand

    bands = list(GradeBand.objects.filter(school=school))
    return BandScale(bands) if bands else PercentScale()
