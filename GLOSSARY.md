# Glossary

| Term               | User-facing name     | Code name        | Definition                                                                                                                       |
| ------------------ | -------------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Campaign           | Campanha             | `campaign`       | One folder of videos, with its ROIs and results.                                                                                 |
| Day                | Dia                  | `day`            | The `YYYY-MM-DD` folder a video is stored in.                                                                                    |
| Disabled day       | Dia desativado       | `disabled_day`   | A day whose runs a job skips. Its existing results are kept.                                                                     |
| Compared campaign  | Campanha comparada   | `Comparison`     | Another campaign shown next to the analysed one on the analysis page.                                                            |
| Day of campaign    | Dia N                | `numbered_days`  | Days counted from a campaign's first day with data, which lines up two compared campaigns.                                       |
| Capture time       | –                    | `captured_at`    | When a video was recorded, read from its file name.                                                                              |
| Plant              | Planta               | `plant_id`       | Plant 1 is on the left of the frame and plant 2 on the right.                                                                    |
| ROI                | ROI                  | `Roi`            | Rectangle of the frame, in video pixels, that isolates one plant. A campaign has one per plant, used for all of its videos.      |
| Run                | Execução             | `Run`            | The estimation for one (video, plant) pair. A video has two runs.                                                                |
| Run history        | Histórico            | `run_history`    | Every result ever written for a run, with its end time. The current result of a run is its latest entry.                         |
| Job                | Processamento        | `job`            | One batch of runs, executed by the worker process.                                                                               |
| Export             | Exportação           | `CampaignExport` | One JSON file with everything that belongs to a campaign except its videos and job history. Importing it recreates the campaign. |
| Dominant frequency | Frequência dominante | `frequency`      | Result of a run, in Hz. See `docs/methodology.md`.                                                                               |
