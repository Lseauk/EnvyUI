import json
import urllib.parse

import click
import re
from http.cookiejar import CookieJar


from typing import Any, Optional, Union

from envied.core.utils.collections import as_list

from envied.core.credential import Credential
from envied.core.tracks.subtitle import Subtitle
from envied.core.manifests import DASH
from envied.core.service import Service
from envied.core.titles import Episode, Movie, Movies, Series
from envied.core.tracks import Tracks


class CRAVE(Service):
    """
    Service code for Bell Media's Crave streaming service (https://crave.ca).

    \b
    Authorization: Credentials
    Security: UHD@-- HD@L3, doesn't care about releases.

    TODO: Movies are not yet supported
    NOTE: Devine accepts "def authenticate(self, cookies: Optional[CookieJar] = None, credential: Optional[Credential] = None) -> Optional[str]:" as default.
    But we can also use the default "def configure(self)" method which is used in VT(Vinetrimmer) but just adding the others inside configure.ie like this,
    "def config(self, cookies: Optional[CookieJar] = None, credential: Optional[Credential] = None) -> Optional[str]:"
    """

    ALIASES = ["CRAV", "crave"]  # CRAV is unconfirmed but likely candidate, been in use for a few months
    GEOFENCE = ["ca"]
    TITLE_RE = r"^(?:https?://(?:www\.)?crave\.ca(?:/[a-z]{2})?/(?:movies|tv-shows)/)?(?P<id>[a-z0-9-]+)"

    @staticmethod
    @click.command(name="CRAVE", short_help="https://crave.ca")
    @click.argument("title", type=str, required=False)
    @click.pass_context
    def cli(ctx: click.Context, **kwargs: Any) -> "CRAVE":
        return CRAVE(ctx, **kwargs)

    def __init__(self, ctx, title):
        super().__init__(ctx)
        self.parse_title(ctx, title)

        self.vcodec = ctx.parent.params["vcodec"]

        self.access_token = None
        self.credential = None

    def authenticate(
        self, cookies: Optional[CookieJar] = None, credential: Optional[Credential] = None
    ) -> Optional[str]:
        if cookies:
            self.session.cookies.update(cookies)
        if self.credential is None and credential:
            self.credential = credential

        headers = {"Authorization": self.config["headers"]["authorization"]}

        body = {
            "username": self.credential.username,
            "password": self.credential.password,
            "grant_type": "password",
        }

        r = self.session.post(
            "https://account.bellmedia.ca/api/login/v2.1",
            headers=headers,
            data=body,
        )

        self.log.info(" + Logging in")
        self.log.info(f"Fetching Axis title ID based on provided path: {self.title}")
        axis_id = self.get_axis_id(f"/tv-shows/{self.title}") or self.get_axis_id(f"/movies/{self.title}")
        self.title = axis_id
        self.log.info(f" + Obtained: {self.title}")

        try:
            response_data = r.json()
            self.access_token = response_data.get("access_token")
            if not self.access_token:
                raise ValueError(f"Login failed: {response_data}")
            return self.access_token
        except json.JSONDecodeError:
            raise ValueError(f"Failed to parse login response: {r.text}")

    def get_titles(self):
        # Fetch main title information
        res = self.session.post(
            url="https://www.crave.ca/space-graphql/graphql",
            json={
                "operationName": "axisMedia",
                "variables": {"axisMediaId": self.title},
                "query": """
                query axisMedia($axisMediaId: ID!) {
                    contentData: axisMedia(id: $axisMediaId) {
                        id
                        axisId
                        title
                        originalSpokenLanguage
                        firstPlayableContent {
                            id
                            title
                            axisId
                            path
                            seasonNumber
                            episodeNumber
                        }
                        mediaType
                        firstAirYear
                        seasons {
                            title
                            id
                            seasonNumber
                        }
                    }
                }
                """,
            },
        ).json()

        # Ensure the response structure is valid
        if "data" not in res or "contentData" not in res["data"]:
            raise ValueError("Invalid response structure from Crave API")

        title_information = res["data"]["contentData"]

        # Handle movie titles
        if title_information["mediaType"] == "MOVIE":
            return Movies(
                [
                    Movie(
                        id_=self.title,
                        service=self.__class__,
                        name=title_information["title"],
                        year=title_information.get("firstAirYear"),
                        language=title_information.get("originalSpokenLanguage"),
                        data=title_information["firstPlayableContent"],
                    )
                ]
            )

        # Fetch episodes for each season
        seasons = title_information.get("seasons", [])
        episodes = []
        for season in seasons:
            res = self.session.post(
                url="https://www.crave.ca/space-graphql/graphql",
                json={
                    "operationName": "season",
                    "variables": {"seasonId": season["id"]},
                    "query": """
                    query season($seasonId: ID!) {
                        axisSeason(id: $seasonId) {
                            episodes {
                                axisId
                                title
                                contentType
                                seasonNumber
                                episodeNumber
                                axisPlaybackLanguages {
                                    language
                                }
                            }
                        }
                    }
                    """,
                },
            ).json()

            # Ensure the response contains episode data
            if "data" in res and "axisSeason" in res["data"]:
                season_episodes = res["data"]["axisSeason"].get("episodes", [])
                episodes.extend(
                    Episode(
                        id_=episode.get("axisId"),
                        title=title_information["title"],
                        year=title_information.get("firstAirYear"),
                        season=episode.get("seasonNumber"),
                        number=episode.get("episodeNumber"),
                        name=episode.get("title"),
                        language=title_information.get("originalSpokenLanguage"),
                        service=self.__class__,
                        data=episode,
                    )
                    for episode in season_episodes
                    if episode["contentType"] == "EPISODE"
                )

        return Series(episodes)

    def get_tracks(self, title: Union[Movie, Episode]) -> Tracks:
        tracks = Tracks()
        package_id = self.session.get(
            url=self.config["endpoints"]["content_packages"].format(title_id=title.data["axisId"]),
            params={"$lang": "en"},
        ).json()["Items"][0]["Id"]

        mpd_url = self.config["endpoints"]["manifest"].format(title_id=title.data["axisId"], package_id=package_id)
        r = self.session.get(
            mpd_url,
            params={
                "jwt": self.access_token,
                "filter": "25" if self.vcodec == "H265" else "24",
            },
        )
        try:
            mpd_data = r.json()
        except json.JSONDecodeError:
            mpd_data = r.text
        else:
            raise Exception(
                "Crave reported an error when obtaining the MPD Manifest.\n"
                + f"{mpd_data['Message']} ({mpd_data['ErrorCode']})"
            )

        tracks.add(DASH.from_text(mpd_data, url=mpd_url).to_tracks(title.language))

        #tracks.add(
        #    Subtitle(
        #        id_=f"{title.data['axisId']}_{package_id}_sub",
        #        url=(
        #            f"{self.config['endpoints']['srt'].format(title_id=title.data['axisId'], package_id=package_id)}?"
        #            + urllib.parse.urlencode({"jwt": urllib.parse.quote_plus(self.access_token)})
        #        ),
        #        codec=Subtitle.Codec.SubRip,
        #        language=None,  # TODO: Extract proper language from subtitle metadata
        #        sdh=True,
        #    )
        #)

        return tracks

    def get_chapters(self, title):
        return []

    def get_widevine_service_certificate(self, **_):
        return None  # will use common privacy cert

    def get_widevine_license(self, challenge, **_):
        return self.session.post(
            url=self.config["endpoints"]["license"],
            data=challenge,  # expects bytes
        ).content

    def get_axis_id(self, path):
        res = self.session.post(
            url="https://www.crave.ca/space-graphql/graphql",
            json={
                "operationName": "resolvePath",
                "variables": {"path": path},
                "query": """
                query resolvePath($path: String!) {
                    resolvedPath(path: $path) {
                        lastSegment {
                            content {
                                id
                            }
                        }
                    }
                }
                """,
            },
        ).json()
        if "errors" in res:
            if res["errors"][0]["extensions"]["code"] == "NOT_FOUND":
                return None
            raise ValueError("Unknown error has occurred when trying to obtain the Axis ID for: " + path)
        return res["data"]["resolvedPath"]["lastSegment"]["content"]["id"]

    def parse_title(self, ctx, title):
        title = title or ctx.parent.params.get("title")
        if not title:
            self.log.error(" - No title ID specified")
        if not getattr(self, "TITLE_RE"):
            self.title = title
            return {}
        for regex in as_list(self.TITLE_RE):
            m = re.search(regex, title)
        if m:
            self.title = m.group("id")
            return m.groupdict()
        self.log.warning(f" - Unable to parse title ID {title!r}, using as-is")
        self.title = title
