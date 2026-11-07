# Methodology

`methodology.py` is a port of `scripts/prepare.py`, `scripts/bss_pipeline.py` and `get_dominant_source` from the marajo repository, as used by `notebooks/main_acai_estresse_hidrico.ipynb`.

## Steps

For each (video, plant) pair:

1. **Preprocess:** read up to 630 frames, crop to the ROI, convert to grayscale, scale to 20% with area interpolation, and re-encode as MPEG-4 in a temporary file.
2. **PCA:** centered over time, keeping the first 10 components.
3. **Source separation:** the temporal predictability algorithm (`CP_alg` in marajo), with masks of half-life 1 and 900000 frames, limited to 50 frames.
4. **Peak per source:** the highest peak of each source's power spectrum, with the frequency axis built from the fps of the re-encoded file.
5. **Dominant source:** the highest power among the sources whose peak is above 1 Hz. A run with none has no frequency.

| Parameter | Value |
|---|---|
| `N_PC` | 10 |
| `NFRAMES_OUT` | 630 |
| `SCALE_OUT` | 0.2 |
| `FREQUENCY_THRESHOLD` | 1 Hz |

The fps always comes from the video container. The campaigns recorded in slow motion in marajo store a wrong fps in the container and are not supported.

## Equivalence with marajo

The numeric steps must stay equivalent, otherwise results stop being comparable. `test_matches_the_original_code` pins the output on a synthetic video to what the marajo code returns for it.

With the ROIs in `rois/acai_estresse_hidrico.json`, frequency, source and amplitude matched marajo's `results.csv` in all 80 runs of a random sample of 40 videos from `semana2`, and in all 12 runs of a batch started from the interface. The `semana1` videos were not on disk, so they were not compared. The planthz environment uses the same versions of NumPy, SciPy, scikit-learn and OpenCV as the marajo one, except that it installs the headless build of OpenCV.

The only behavior added to the numeric path: a ROI that does not fit in the frame fails the run, where the original would clip the crop and stretch it back to the expected size.

## Source maps

The video page shows where in the ROI each source comes from, as marajo's `notebooks/main_overlay.ipynb` does with `compute_source_spatial_maps` and the overlay videos in `scripts/services.py`. Only for inspection: the maps are not part of the results or the export.

- **Map:** the least-squares mixing `A` that rebuilds the PCA components from the sources (`components ≈ sources @ A`), applied to the PCA scores of each pixel: `scores[:, :N_PC] @ A.T`. One weight per pixel of the preprocessed ROI (20% scale, as read back from the encoder, which rounds odd sizes down to even ones).
- **Display:** sign and scale of a source are arbitrary, so the map shows `|weight|` normalized to its maximum, colored with the JET colormap. Pixels below 0.15 are transparent, and the opacity rises from there to 1.
- **Animation:** the color and opacity of each pixel are multiplied by `|signal|` normalized to its maximum, frame by frame at the video's fps, as `create_dynamic_source_overlay_video` does. It covers the 630 analysed frames, over the still frame shown on the page.
- **When:** during processing, in the same pass as the estimate, since the video may leave the disk afterwards. See `docs/architecture.md`.
- **ROI:** the map is computed with the ROI stored in the result, so it explains the frequency on the page even when the campaign's ROI has since changed.

The sources and their peaks come from the same steps as the estimate, so the dominant source of the maps is the one stored in the result.

## What the original notebook does and planthz does not

Removed on purpose until the analyses are defined. Only the distinction between plant 1 and plant 2 remains:

- Weeks, fan condition, phase and camera outage, which were constants of one campaign.
- The standard exposure filter and the capture manifest.
- Leaf crossing detection.
- Outlier removal, smoothed curves and LaTeX tables.

## Limitations

- **The dominant source is not always the true frequency:** the source separation returns sources of arbitrary scale, and the dominant one is picked by comparing their power. On synthetic videos where half of the frame oscillates uniformly in brightness, both planthz and the marajo code returned the same wrong values: 3 Hz came out as 2.2, 6, 13 or 23.6 Hz depending on length, frame rate and noise, and 5 Hz as 1.8, 9.9, 10 or 14.7 Hz. Only one of the 10 cases tried recovered the true frequency. Those videos are unlike real plants, so this does not measure the error on real data, but it means a frequency should not be taken as validated by the method alone.
- **Mask length is in frames:** the masks in step 3 last different times at different frame rates, so results from videos with another frame rate are not directly comparable.
- **Fixed number of frames:** a video longer than 630 frames is truncated, which also changes the frequency resolution.
