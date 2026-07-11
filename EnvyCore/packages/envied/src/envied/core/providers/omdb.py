from __future__ import annotations

from typing import Optional, Union

import requests

from envied.core.config import config
from envied.core.providers._base import ExternalIds, MetadataProvider, MetadataResult, fuzzy_match, _strip_year


class OmdbProvider(MetadataProvider):
    """OMDb (Open Movie Database) metadata provider."""

    NAME = "omdb"
    REQUIRES_KEY = True
    BASE_URL = "http://www.omdbapi.com"

    def is_available(self) -> bool:
        return bool(config.omdb_api_key)

    @property
    def _api_key(self) -> str:
        return config.omdb_api_key

    def search(self, title: str, year: Optional[int], kind: str) -> Optional[MetadataResult]:
        search_title = _strip_year(title)
        omdb_type = "movie" if kind == "movie" else "series"
        self.log.debug("Searching OMDb for %r (%s, %s)", search_title, omdb_type, year)

        params: dict[str, str | int] = {
            "apikey": self._api_key,
            "t": search_title,
            "type": omdb_type,
        }
        if year is not None:
            params["y"] = year

        try:
            r = self.session.get(self.BASE_URL, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            self.log.debug("OMDb search failed: %s", exc)
            return None

        if data.get("Response") != "True":
            self.log.debug("OMDb returned no result for %r: %s", title, data.get("Error"))
            return None

        result_title = data.get("Title")
        if not result_title or not fuzzy_match(result_title, title):
            self.log.debug("OMDb title mismatch: searched %r, got %r", title, result_title)
            return None

        imdb_id = data.get("imdbID")
        year_str = data.get("Year", "")
        result_year: Optional[int] = None
        if year_str and year_str[:4].isdigit():
            result_year = int(year_str[:4])

        self.log.debug("OMDb -> %s (ID %s)", result_title, imdb_id)

        return MetadataResult(
            title=result_title,
            year=result_year,
            kind=kind,
            external_ids=ExternalIds(imdb_id=imdb_id),
            source="omdb",
            raw=data,
        )

    def get_by_id(self, provider_id: Union[int, str], kind: str) -> Optional[MetadataResult]:
        imdb_id = str(provider_id)
        self.log.debug("Fetching OMDb title by IMDB ID %s", imdb_id)

        try:
            r = self.session.get(
                self.BASE_URL,
                params={"apikey": self._api_key, "i": imdb_id},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            self.log.debug("OMDb get_by_id failed: %s", exc)
            return None

        if data.get("Response") != "True":
            return None

        year_str = data.get("Year", "")
        result_year: Optional[int] = None
        if year_str and year_str[:4].isdigit():
            result_year = int(year_str[:4])

        return MetadataResult(
            title=data.get("Title"),
            year=result_year,
            kind=kind,
            external_ids=ExternalIds(imdb_id=data.get("imdbID")),
            source="omdb",
            raw=data,
        )

    def get_external_ids(self, provider_id: Union[int, str], kind: str) -> ExternalIds:
        return ExternalIds(imdb_id=str(provider_id))
