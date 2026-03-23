from __future__ import annotations

__all__ = ["github_trending_page", "github_trending_repo_html"]


def github_trending_repo_html(
    owner: str = "openai",
    name: str = "codex",
    description: str = "An AI pair programmer",
    language: str = "Python",
    total_stars: str = "45,231",
    forks: str = "1,234",
    stars_in_period: str = "1,523 stars today",
) -> str:
    """Build one <article> block matching GitHub Trending HTML structure."""
    lang_span = ""
    if language:
        lang_span = f"""
        <span class="d-inline-block ml-0 mr-3">
          <span class="repo-language-color" style="background-color: #3572A5"></span>
          <span itemprop="programmingLanguage">{language}</span>
        </span>"""

    return f"""
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/{owner}/{name}">
      <span>{owner} /</span>
      <span class="text-normal">{name}</span>
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">{description}</p>
  <div class="f6 color-fg-muted mt-2">{lang_span}
    <a class="Link--muted d-inline-block mr-3" href="/{owner}/{name}/stargazers">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      {total_stars}
    </a>
    <a class="Link--muted d-inline-block mr-3" href="/{owner}/{name}/forks">
      <svg class="octicon octicon-repo-forked" aria-label="fork"></svg>
      {forks}
    </a>
    <span class="d-inline-block float-sm-right">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      {stars_in_period}
    </span>
  </div>
</article>"""


def github_trending_page(*repo_htmls: str) -> str:
    """Wrap repo article blocks into a full trending page."""
    body = "\n".join(repo_htmls)
    return f"<html><body>{body}</body></html>"
