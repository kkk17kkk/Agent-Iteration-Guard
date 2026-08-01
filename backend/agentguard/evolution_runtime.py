from __future__ import annotations

import json
import time
from typing import Protocol

from .domain import (
    EvaluationHypothesis,
    EvolutionAgentRun,
    EvolutionModelCall,
    EvolutionObservation,
    EvolutionProviderUsage,
    EvolutionToolCall,
    EvolutionToolProposal,
    ProviderBinding,
    now,
)
from .provider_runtime import ProviderRuntimeError, ProviderTurn
from .store import Store
from .targets import TargetAdapter, TargetInfrastructureError, payload_fingerprint


class ToolCallingProvider(Protocol):
    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ProviderTurn: ...


SYSTEM_PROMPT = (
    "You are the Agent Iteration Guard control-plane Evolution Agent. "
    "Use only the supplied tools and their observations. Call exactly one tool per turn, then wait for its observation before choosing the next tool. "
    "You may submit an evidence-linked hypothesis or insufficient_evidence, "
    "but you must not decide ground truth, verifier verdicts, or release status. Read evidence before claiming a supported difference."
)


class EvolutionAgentRuntime:
    def __init__(
        self, store: Store, binding: ProviderBinding, provider: ToolCallingProvider, adapter: TargetAdapter,
        *, system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.store = store
        self.binding = binding
        self.provider = provider
        self.adapter = adapter
        self.system_prompt = system_prompt
        self.tool_specs = adapter.tool_specs()
        self.allowed_tools = [str(item["function"]["name"]) for item in self.tool_specs]

    def start(self, *, project_id: str, evolution_case_id: str, objective: str) -> EvolutionAgentRun:
        run = EvolutionAgentRun(
            project_id=project_id,
            evolution_case_id=evolution_case_id,
            provider_binding_id=self.binding.provider_binding_id,
            objective=objective,
            allowed_tools=self.allowed_tools,
        )
        self.store.save("evolution_agent_run", run.evolution_agent_run_id, project_id, run)
        return self.resume(run.evolution_agent_run_id)

    def resume(self, run_id: str) -> EvolutionAgentRun:
        run = self.store.get("evolution_agent_run", run_id, EvolutionAgentRun)
        if not run:
            raise ValueError("Evolution Agent run not found")
        if run.provider_binding_id != self.binding.provider_binding_id:
            raise ValueError("Evolution Agent run and ProviderBinding do not match")
        if run.status in {"completed", "failed", "infrastructure_blocked"}:
            return run
        started = time.monotonic()
        self._restore_adapter(run)
        run = run.model_copy(update={"status": "running", "updated_at": now()})
        self._save_run(run)
        while run.status == "running":
            pending = self._pending_proposal(run)
            if pending:
                model_call, proposal = pending
                run = self._execute_proposal(run, model_call, proposal)
                if run.status != "running":
                    return run
                continue
            if len(run.model_call_ids) >= self.binding.max_model_calls:
                return self._terminal(run, "failed", "model_call_limit")
            if len(run.tool_call_ids) >= self.binding.max_tool_calls:
                return self._terminal(run, "failed", "tool_call_limit")
            if time.monotonic() - started >= self.binding.max_wall_time_seconds:
                return self._terminal(run, "infrastructure_blocked", "wall_time_budget_exhausted")
            messages = self._messages(run)
            if not self._request_fits_budget(run, messages):
                return self._terminal(run, "infrastructure_blocked", "cost_budget_exhausted")
            try:
                turn = self.provider.complete(messages, self.tool_specs)
            except ProviderRuntimeError as error:
                model_call = EvolutionModelCall(
                    project_id=run.project_id,
                    evolution_agent_run_id=run.evolution_agent_run_id,
                    sequence=len(run.model_call_ids) + 1,
                    provider=self.binding.provider,
                    model=self.binding.model,
                    request_fingerprint=payload_fingerprint({"messages": messages, "tools": self.tool_specs}),
                    response_fingerprint=error.response_fingerprint,
                    outcome="provider_error",
                    error=str(error),
                )
                self.store.save("evolution_model_call", model_call.evolution_model_call_id, run.project_id, model_call)
                run = run.model_copy(update={"model_call_ids": [*run.model_call_ids, model_call.evolution_model_call_id]})
                self._save_run(run)
                return self._terminal(run, "infrastructure_blocked", "provider_error")
            run = self._record_turn(run, turn)
            if run.status != "running":
                return run
            model_call = self.store.get("evolution_model_call", run.model_call_ids[-1], EvolutionModelCall)
            assert model_call
            batch_error = self._validate_proposal_batch(run, model_call)
            if batch_error:
                return self._terminal(run, "failed", batch_error)
        return run

    def _execute_proposal(
        self, run: EvolutionAgentRun, model_call: EvolutionModelCall, proposal: EvolutionToolProposal
    ) -> EvolutionAgentRun:
        name = proposal.name
        arguments = proposal.arguments
        if name not in self.allowed_tools:
            return self._record_rejected_tool(
                run, model_call.evolution_model_call_id, proposal.native_tool_call_id, name, arguments, "tool_not_allowed"
            )
        try:
            target_observation = self.adapter.execute(name, arguments)
        except TargetInfrastructureError as error:
            return self._record_failed_tool(
                run, model_call.evolution_model_call_id, proposal.native_tool_call_id,
                name, arguments, f"target_infrastructure_error:{error}", True,
            )
        except (TypeError, ValueError) as error:
            return self._record_failed_tool(
                run, model_call.evolution_model_call_id, proposal.native_tool_call_id,
                name, arguments, str(error), False,
            )
        tool_call = EvolutionToolCall(
            project_id=run.project_id,
            evolution_agent_run_id=run.evolution_agent_run_id,
            model_call_id=model_call.evolution_model_call_id,
            native_tool_call_id=proposal.native_tool_call_id,
            sequence=len(run.tool_call_ids) + 1,
            name=name,
            arguments=arguments,
            status="executed",
        )
        observation = EvolutionObservation(
            project_id=run.project_id,
            evolution_agent_run_id=run.evolution_agent_run_id,
            tool_call_id=tool_call.evolution_tool_call_id,
            sequence=len(run.observation_ids) + 1,
            payload=target_observation.payload,
            state_fingerprint=payload_fingerprint(target_observation.payload),
            terminal=target_observation.terminal,
        )
        tool_call = tool_call.model_copy(update={"observation_id": observation.evolution_observation_id})
        self.store.save_many([
            ("evolution_tool_call", tool_call.evolution_tool_call_id, run.project_id, tool_call),
            ("evolution_observation", observation.evolution_observation_id, run.project_id, observation),
        ])
        run = run.model_copy(update={
            "tool_call_ids": [*run.tool_call_ids, tool_call.evolution_tool_call_id],
            "observation_ids": [*run.observation_ids, observation.evolution_observation_id],
            "updated_at": now(),
        })
        self._save_run(run)
        if observation.terminal:
            return self._complete(run, observation)
        return run

    def _record_turn(self, run: EvolutionAgentRun, turn: ProviderTurn) -> EvolutionAgentRun:
        if self.binding.input_price_per_million_usd is None or self.binding.output_price_per_million_usd is None or not self.binding.pricing_source:
            return self._terminal(run, "infrastructure_blocked", "pricing_snapshot_missing")
        cache_price = self.binding.cache_hit_price_per_million_usd
        uncached = max(0, turn.input_tokens - turn.cache_hit_tokens)
        cost = (
            uncached * self.binding.input_price_per_million_usd
            + turn.cache_hit_tokens * (cache_price if cache_price is not None else self.binding.input_price_per_million_usd)
            + turn.output_tokens * self.binding.output_price_per_million_usd
        ) / 1_000_000
        usage = EvolutionProviderUsage(
            project_id=run.project_id,
            evolution_agent_run_id=run.evolution_agent_run_id,
            provider=self.binding.provider,
            model=self.binding.model,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            cache_hit_tokens=turn.cache_hit_tokens,
            total_cost_usd=cost,
            pricing_source=self.binding.pricing_source,
        )
        proposal = turn.tool_calls[0] if turn.tool_calls else None
        proposals = [
            EvolutionToolProposal(native_tool_call_id=item.call_id, name=item.name, arguments=item.arguments)
            for item in turn.tool_calls
        ]
        model_call = EvolutionModelCall(
            project_id=run.project_id,
            evolution_agent_run_id=run.evolution_agent_run_id,
            sequence=len(run.model_call_ids) + 1,
            provider=self.binding.provider,
            model=self.binding.model,
            provider_request_id=turn.request_id,
            native_tool_call_id=proposal.call_id if proposal else None,
            proposed_tool_count=len(turn.tool_calls),
            proposed_tool_names=[item.name for item in turn.tool_calls],
            tool_proposals=proposals,
            tool_name=proposal.name if proposal else None,
            tool_arguments=proposal.arguments if proposal else {},
            finish_reason=turn.finish_reason,
            request_fingerprint=turn.request_fingerprint,
            response_fingerprint=turn.response_fingerprint,
            usage_id=usage.evolution_provider_usage_id,
            outcome="tool_call" if proposal else "invalid_response",
            error=None if proposal else "provider must return at least one tool call",
        )
        self.store.save_many([
            ("evolution_provider_usage", usage.evolution_provider_usage_id, run.project_id, usage),
            ("evolution_model_call", model_call.evolution_model_call_id, run.project_id, model_call),
        ])
        spent = run.spent_cost_usd + cost
        run = run.model_copy(update={
            "model_call_ids": [*run.model_call_ids, model_call.evolution_model_call_id],
            "spent_cost_usd": spent,
            "updated_at": now(),
        })
        self._save_run(run)
        if spent > self.binding.batch_budget_usd:
            return self._terminal(run, "infrastructure_blocked", "observed_cost_exceeded_budget")
        if not proposals:
            return self._terminal(run, "failed", "invalid_provider_tool_count")
        return run

    def _messages(self, run: EvolutionAgentRun) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": run.objective},
        ]
        calls = {item.evolution_model_call_id: item for item in self.store.list("evolution_model_call", EvolutionModelCall, run.project_id)}
        tool_calls = {item.evolution_tool_call_id: item for item in self.store.list("evolution_tool_call", EvolutionToolCall, run.project_id)}
        observations = {item.evolution_observation_id: item for item in self.store.list("evolution_observation", EvolutionObservation, run.project_id)}
        for model_call_id in run.model_call_ids:
            model_call = calls[model_call_id]
            executed = [tool_calls[item] for item in run.tool_call_ids if tool_calls[item].model_call_id == model_call_id]
            if not executed:
                continue
            proposals = self._model_proposals(model_call)
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": proposal.native_tool_call_id,
                    "type": "function",
                    "function": {"name": proposal.name, "arguments": json.dumps(proposal.arguments, ensure_ascii=False)},
                } for proposal in proposals],
            })
            by_native_id = {item.native_tool_call_id: item for item in executed}
            for proposal in proposals:
                tool_call = by_native_id.get(proposal.native_tool_call_id)
                if not tool_call:
                    continue
                observation = observations[tool_call.observation_id] if tool_call.observation_id else None
                messages.append({
                    "role": "tool",
                    "tool_call_id": proposal.native_tool_call_id,
                    "content": json.dumps(observation.payload if observation else {"error": tool_call.error}, ensure_ascii=False),
                })
        return messages

    def _restore_adapter(self, run: EvolutionAgentRun) -> None:
        restore = getattr(self.adapter, "restore", None)
        if not restore:
            return
        observations = [
            self.store.get("evolution_observation", item, EvolutionObservation)
            for item in run.observation_ids
        ]
        restore([item.payload for item in observations if item])

    def _pending_proposal(self, run: EvolutionAgentRun) -> tuple[EvolutionModelCall, EvolutionToolProposal] | None:
        if not run.model_call_ids:
            return None
        latest = self.store.get("evolution_model_call", run.model_call_ids[-1], EvolutionModelCall)
        if latest and latest.outcome == "tool_call":
            completed_native_ids = {
                item.native_tool_call_id
                for item in self.store.list("evolution_tool_call", EvolutionToolCall, run.project_id)
                if item.evolution_agent_run_id == run.evolution_agent_run_id
                and item.model_call_id == latest.evolution_model_call_id
                and item.native_tool_call_id
            }
            for proposal in self._model_proposals(latest):
                if proposal.native_tool_call_id not in completed_native_ids:
                    return latest, proposal
        return None

    def _validate_proposal_batch(self, run: EvolutionAgentRun, model_call: EvolutionModelCall) -> str | None:
        proposals = self._model_proposals(model_call)
        if not proposals:
            return "invalid_provider_tool_count"
        if len(run.tool_call_ids) + len(proposals) > self.binding.max_tool_calls:
            return "tool_call_limit"
        if any(item.name not in self.allowed_tools for item in proposals):
            return "tool_not_allowed"
        terminal = {"submit_evaluation_hypothesis", "submit_insufficient_evidence"}
        if len(proposals) > 1 and any(item.name in terminal for item in proposals):
            return "terminal_tool_must_be_single"
        return None

    @staticmethod
    def _model_proposals(model_call: EvolutionModelCall) -> list[EvolutionToolProposal]:
        if model_call.tool_proposals:
            return model_call.tool_proposals
        if model_call.native_tool_call_id and model_call.tool_name:
            return [EvolutionToolProposal(
                native_tool_call_id=model_call.native_tool_call_id,
                name=model_call.tool_name,
                arguments=model_call.tool_arguments,
            )]
        return []

    def _request_fits_budget(self, run: EvolutionAgentRun, messages: list[dict[str, object]]) -> bool:
        if self.binding.input_price_per_million_usd is None or self.binding.output_price_per_million_usd is None:
            return False
        input_upper_bound = len(json.dumps({"messages": messages, "tools": self.tool_specs}, ensure_ascii=False).encode("utf-8"))
        maximum = (
            input_upper_bound * self.binding.input_price_per_million_usd
            + self.binding.max_output_tokens * self.binding.output_price_per_million_usd
        ) / 1_000_000
        return run.spent_cost_usd + maximum <= self.binding.batch_budget_usd

    def _complete(self, run: EvolutionAgentRun, observation: EvolutionObservation) -> EvolutionAgentRun:
        complete_terminal = getattr(self.adapter, "complete_terminal", None)
        if complete_terminal:
            artifact = complete_terminal(run.project_id, run.evolution_agent_run_id, observation.payload)
            run = run.model_copy(update={
                "terminal_artifact_kind": artifact.kind,
                "terminal_artifact_id": artifact.record_id,
            })
            return self._terminal(run, "completed", artifact.terminal_reason)
        payload = observation.payload.get("hypothesis")
        if not isinstance(payload, dict):
            return self._terminal(run, "failed", "terminal_observation_missing_hypothesis")
        try:
            kind = str(payload["kind"])
            hypothesis = EvaluationHypothesis(
                project_id=run.project_id,
                evolution_agent_run_id=run.evolution_agent_run_id,
                kind=kind,  # type: ignore[arg-type]
                summary=str(payload["summary"]),
                evidence_refs=[str(item) for item in payload.get("evidence_refs", [])],
                uncertainty=str(payload["uncertainty"]),
                evidence_level="inferred" if kind == "hypothesis" else "unresolved",
            )
        except (KeyError, TypeError, ValueError) as error:
            return self._terminal(run, "failed", f"invalid_hypothesis:{error}")
        self.store.save("evaluation_hypothesis", hypothesis.evaluation_hypothesis_id, run.project_id, hypothesis)
        run = run.model_copy(update={"hypothesis_id": hypothesis.evaluation_hypothesis_id})
        return self._terminal(run, "completed", kind)

    def _record_rejected_tool(
        self, run: EvolutionAgentRun, model_call_id: str, native_tool_call_id: str,
        name: str, arguments: dict[str, object], reason: str,
    ) -> EvolutionAgentRun:
        return self._record_failed_tool(
            run, model_call_id, native_tool_call_id, name, arguments, reason, False, status="rejected"
        )

    def _record_failed_tool(
        self,
        run: EvolutionAgentRun,
        model_call_id: str,
        native_tool_call_id: str,
        name: str,
        arguments: dict[str, object],
        reason: str,
        infrastructure: bool,
        *,
        status: str = "failed",
    ) -> EvolutionAgentRun:
        tool_call = EvolutionToolCall(
            project_id=run.project_id,
            evolution_agent_run_id=run.evolution_agent_run_id,
            model_call_id=model_call_id,
            native_tool_call_id=native_tool_call_id,
            sequence=len(run.tool_call_ids) + 1,
            name=name,
            arguments=arguments,
            status=status,  # type: ignore[arg-type]
            error=reason,
        )
        self.store.save("evolution_tool_call", tool_call.evolution_tool_call_id, run.project_id, tool_call)
        run = run.model_copy(update={"tool_call_ids": [*run.tool_call_ids, tool_call.evolution_tool_call_id]})
        self._save_run(run)
        return self._terminal(run, "infrastructure_blocked" if infrastructure else "failed", reason)

    def _terminal(self, run: EvolutionAgentRun, status: str, reason: str) -> EvolutionAgentRun:
        run = run.model_copy(update={"status": status, "terminal_reason": reason, "updated_at": now()})
        self._save_run(run)
        return run

    def _save_run(self, run: EvolutionAgentRun) -> None:
        self.store.save("evolution_agent_run", run.evolution_agent_run_id, run.project_id, run)
