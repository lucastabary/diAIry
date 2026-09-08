"""The diAIry command line.

The nightly batch is `diairy run`. Everything else exists so that a human can
look at what the batch did and disagree with it.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from diairy.cli.context import build_context
from diairy.cli.web import address, run_server
from diairy.core.config import load_settings
from diairy.core.errors import DiairyError
from diairy.nlp.factory import resolve_profile
from diairy.store.graph import graph_backend_available
from diairy.vault.git import VaultGit

app = typer.Typer(
    name="diairy",
    help="A fully local journal that reads itself into a knowledge graph.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)

models_app = typer.Typer(
    name="models",
    help="Manage downloaded model weights.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(models_app)

EXIT_FAILURE = 1


def _fail(exc: DiairyError) -> None:
    error_console.print(f"[bold red]error[/bold red] {exc}")
    raise typer.Exit(EXIT_FAILURE)


@app.command()
def init() -> None:
    """Create the vault, put it under git, and set up the derived store."""
    try:
        with build_context(with_graph=False) as ctx:
            ctx.settings.vault_path.mkdir(parents=True, exist_ok=True)
            VaultGit(ctx.settings.vault_path).init()
            console.print(
                Panel.fit(
                    f"[bold]vault[/bold]    {ctx.settings.vault_path}\n"
                    f"[bold]store[/bold]    {ctx.settings.db_path}\n"
                    f"[bold]profile[/bold]  {ctx.settings.model_profile}",
                    title="diAIry is ready",
                )
            )
            console.print("Write Markdown in the vault, then run [bold]diairy run[/bold].")
    except DiairyError as exc:
        _fail(exc)


@app.command()
def ingest() -> None:
    """Read the vault and record what changed. No model involved."""
    try:
        with build_context(with_graph=False) as ctx:
            stats = ctx.pipeline().ingest()
    except DiairyError as exc:
        _fail(exc)
        return
    console.print(
        f"seen [bold]{stats.documents_seen}[/bold] documents, "
        f"[bold]{stats.versions_new}[/bold] new versions, "
        f"[bold]{stats.chunks_new}[/bold] new chunks, "
        f"[bold]{stats.facts_superseded}[/bold] facts superseded"
    )
    if stats.git_commit:
        console.print(f"vault snapshot [dim]{stats.git_commit[:12]}[/dim]")


@app.command()
def process(
    limit: Annotated[int | None, typer.Option(help="Stop after this many chunks.")] = None,
) -> None:
    """Extract facts from chunks that have none yet. The expensive stage."""
    try:
        with build_context() as ctx:
            stats = ctx.pipeline().process(limit=limit)
    except DiairyError as exc:
        _fail(exc)
        return
    if not stats.chunks_processed:
        console.print("nothing to process; run [bold]diairy ingest[/bold] first")
        return
    console.print(
        f"processed [bold]{stats.chunks_processed}[/bold] chunks, "
        f"wrote [bold]{stats.facts_written}[/bold] facts, "
        f"rejected [bold]{stats.facts_rejected}[/bold]"
    )
    for reason, count in sorted(stats.rejection_reasons.items()):
        console.print(f"  [yellow]{reason}[/yellow]: {count}")


@app.command()
def run(
    limit: Annotated[
        int | None, typer.Option(help="Stop extraction after this many chunks.")
    ] = None,
) -> None:
    """Run every stage: ingest, extract, embed, project. The nightly batch."""
    try:
        with build_context() as ctx:
            pipeline = ctx.pipeline()
            ingested = pipeline.ingest()
            console.print(f"ingest   {ingested.chunks_new} new chunks")
            processed = pipeline.process(limit=limit)
            console.print(
                f"process  {processed.facts_written} facts ({processed.facts_rejected} rejected)"
            )
            embedded = pipeline.embed()
            if embedded.available:
                console.print(f"embed    {embedded.chunks_embedded} chunks")
            else:
                console.print(f"[yellow]embed    skipped: {embedded.note}[/yellow]")
            projected = pipeline.project()
            console.print(f"project  {projected.concepts} concepts, {projected.edges} edges")
    except DiairyError as exc:
        _fail(exc)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="A question about your journal.")],
    limit: Annotated[int, typer.Option(help="How many passages to retrieve.")] = 8,
) -> None:
    """Ask a question. Every claim in the answer cites a passage."""
    try:
        with build_context() as ctx:
            answer = ctx.answerer().ask(question, limit=limit)
    except DiairyError as exc:
        _fail(exc)
        return

    console.print(Panel(answer.text, title="answer", border_style="cyan"))
    if answer.is_uncited:
        console.print("[yellow]This answer cites no passage. Treat it with suspicion.[/yellow]")
    for passage in answer.cited_passages:
        console.print(f"[dim][{passage.index}] {passage.event_date} · {passage.source_path}[/dim]")
    if not answer.semantic_search_used:
        console.print("[dim]full-text search only: no embeddings indexed yet[/dim]")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Words to look for.")],
    limit: Annotated[int, typer.Option(help="How many passages to show.")] = 10,
) -> None:
    """Search the journal without asking a model to interpret it."""
    try:
        with build_context(with_graph=False) as ctx:
            context = ctx.retriever().retrieve(query, limit=limit)
    except DiairyError as exc:
        _fail(exc)
        return

    if not context.passages:
        console.print("no matches")
        return
    for passage in context.passages:
        marker = "both" if passage.found_by_both else "one"
        console.print(
            f"[bold]{passage.event_date}[/bold] [dim]{passage.source_path} ({marker})[/dim]"
        )
        console.print(passage.text.strip()[:400])
        console.print()


@app.command()
def status() -> None:
    """Show what is in the store, and what still needs processing."""
    try:
        with build_context(with_graph=False) as ctx:
            table = Table(show_header=False, box=None)
            table.add_row("vault", str(ctx.settings.vault_path))
            table.add_row("store", str(ctx.settings.db_path))
            table.add_row("profile", ctx.settings.model_profile)
            table.add_row("extraction model", ctx.profile.extraction.model)
            table.add_row("embedding model", ctx.profile.embedding.model)
            table.add_row("documents", str(ctx.repository.document_count()))
            table.add_row("chunks", str(ctx.repository.chunk_count()))
            table.add_row(
                "chunks awaiting extraction", str(len(ctx.repository.chunks_without_facts()))
            )
            table.add_row("current facts", str(ctx.repository.fact_count()))
            table.add_row(
                "superseded facts",
                str(ctx.repository.fact_count(current_only=False) - ctx.repository.fact_count()),
            )
            table.add_row("concepts", str(ctx.repository.concept_count()))
            table.add_row("vectors indexed", str(ctx.search.indexed_vector_count()))
            table.add_row("graph backend", "available" if graph_backend_available() else "missing")
            console.print(table)

            runs = ctx.repository.recent_runs(limit=5)
            if runs:
                console.print("\n[bold]recent runs[/bold]")
                for entry in runs:
                    console.print(
                        f"  {entry.started_at:%Y-%m-%d %H:%M}  {entry.status:<9} "
                        f"{entry.model or '(no model)'}"
                    )
    except DiairyError as exc:
        _fail(exc)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Address to bind. Loopback by default.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8765,
    write: Annotated[
        bool, typer.Option("--write", help="Let the UI add source entries to the vault.")
    ] = False,
    dev: Annotated[
        bool,
        typer.Option("--dev", help="Unlock the pipeline, graph and test panels (implies --write)."),
    ] = False,
) -> None:
    """Serve the local web UI. A browser window onto what the batch did."""
    url = address(host, port)
    can_write = write or dev
    mode = "[yellow]developer[/yellow]" if dev else ("write" if can_write else "read-only")
    console.print(
        Panel.fit(
            f"[bold]{url}[/bold]\n"
            f"[dim]mode[/dim]     {mode}\n"
            f"[dim]writing[/dim]  {'on' if can_write else 'off'}\n"
            f"[dim]egress[/dim]   loopback only",
            title="diAIry is serving",
        )
    )
    console.print("Open the address above in a browser. [dim]Ctrl-C to stop.[/dim]")
    try:
        run_server(host=host, port=port, dev=dev, write=write)
    except OSError as exc:
        error_console.print(
            f"[bold red]error[/bold red] could not bind {url}: {exc}. "
            f"Try another --port, or stop whatever is already using it."
        )
        raise typer.Exit(EXIT_FAILURE) from exc


@models_app.command("pull")
def models_pull(
    profile: Annotated[
        str | None,
        typer.Option(help="Which profile's model to fetch. Defaults to the configured one."),
    ] = None,
) -> None:
    """Download the transcription model weights for a profile.

    This is the one command in diAIry that reaches the network on purpose, to
    fetch model weights -- exactly like `ollama pull`. It runs *outside* the
    egress guard by design (ADR 0009); every other code path loads weights
    locally only and can never download. Run it once per machine.
    """
    from diairy.nlp.whisper import download_model  # noqa: PLC0415 -- optional extra, lazy by design

    try:
        settings = load_settings(model_profile=profile) if profile else load_settings()
        settings.ensure_directories()
        spec = resolve_profile(settings).transcription
    except DiairyError as exc:
        _fail(exc)
        return

    if spec is None or spec.backend == "fake":
        error_console.print(
            f"[bold red]error[/bold red] profile "
            f"[bold]{settings.model_profile}[/bold] has no downloadable transcription model."
        )
        raise typer.Exit(EXIT_FAILURE)
    if spec.backend != "faster-whisper":
        error_console.print(
            f"[bold red]error[/bold red] don't know how to pull weights for backend "
            f"[bold]{spec.backend}[/bold]. Only faster-whisper is supported here."
        )
        raise typer.Exit(EXIT_FAILURE)

    console.print(
        f"[yellow]reaching the network[/yellow] to fetch [bold]{spec.model}[/bold] "
        f"into {settings.models_dir}"
    )
    try:
        download_model(spec.model, download_root=str(settings.models_dir))
    except DiairyError as exc:
        _fail(exc)
        return
    console.print(
        f"[green]done[/green]. Transcription now works offline on the "
        f"[bold]{settings.model_profile}[/bold] profile."
    )


@app.command()
def inspect(
    chunk_id: Annotated[str, typer.Argument(help="Chunk id, from `diairy search`.")],
) -> None:
    """Show a passage and every fact extracted from it, with its evidence."""
    try:
        with build_context(with_graph=False) as ctx:
            chunk = ctx.repository.get_chunk(chunk_id)
            if chunk is None:
                console.print(f"no chunk with id {chunk_id}")
                raise typer.Exit(EXIT_FAILURE)
            console.print(Panel(chunk.text, title=f"chunk {chunk.id}"))
            facts = ctx.repository.facts_for_chunk(chunk.id, current_only=False)
            if not facts:
                console.print("no facts extracted from this passage yet")
                return
            for fact in facts:
                state = "" if fact.is_current else " [dim](superseded)[/dim]"
                console.print(
                    f"[bold]{fact.subject.label}[/bold] ({fact.subject.type}) "
                    f"--{fact.predicate}--> "
                    f"[bold]{fact.object.label}[/bold] ({fact.object.type}) "
                    f"[dim]{fact.confidence:.2f}[/dim]{state}"
                )
                console.print(f"    [dim]> {fact.evidence.quote}[/dim]")
    except DiairyError as exc:
        _fail(exc)


if __name__ == "__main__":  # pragma: no cover
    app()
