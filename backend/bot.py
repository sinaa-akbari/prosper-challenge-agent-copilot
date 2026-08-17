#
# Standalone voice bot — the starter's entry point, kept working.
#
# `server.py` is the real application: it serves the builder UI and starts a call
# against whichever agent you're editing. This file is the single-agent path via
# Pipecat's own dev runner, which is useful for debugging the voice pipeline on
# its own, without the UI in the way.
#
#   python bot.py            run the seeded demo agent
#   AGENT_ID=my-agent python bot.py
#   AGENT_FLOW=path.json python bot.py
#
# Then open http://localhost:7860/client.
#

import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner
from pipecat_flows import FlowManager

from agent_builder import AgentBuilder, store
from voice import build_llm

# Load .env next to this file, so the bot runs the same from the repo root or backend/.
load_dotenv(Path(__file__).parent / ".env", override=True)

# Which agent to run: an explicit JSON file wins, otherwise one from the store.
AGENT_FLOW = os.environ.get("AGENT_FLOW")
AGENT_ID = os.environ.get("AGENT_ID", "northside-scheduling")


def load_builder() -> AgentBuilder:
    if AGENT_FLOW:
        return AgentBuilder.from_json(AGENT_FLOW)
    if store.exists(AGENT_ID):
        return AgentBuilder.from_dict(store.get_config(AGENT_ID))
    fallback = Path(__file__).parent / "example_flow.json"
    logger.warning(f"No agent '{AGENT_ID}' in the store — falling back to {fallback.name}. "
                   "Run `python seed.py` to create the demo agent.")
    return AgentBuilder.from_json(fallback)


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def run_bot(
    transport: BaseTransport, runner_args: RunnerArguments, builder: AgentBuilder
) -> None:
    config = builder.config
    logger.info(f"Starting '{config.name}' with {len(config.nodes)} nodes on {config.model}")

    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(voice=config.voice_id),
    )
    llm = build_llm(config.model)

    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    flow_manager = FlowManager(
        llm=llm,
        context_aggregator=context_aggregator,
        worker=worker,
        transport=transport,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — starting flow at initial node")
        await flow_manager.initialize(builder.build_initial_node())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Entry point invoked by the Pipecat dev runner (and Pipecat Cloud)."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, load_builder())


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
