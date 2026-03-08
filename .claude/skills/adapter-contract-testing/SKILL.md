---
name: adapter-contract-testing
description: Reference guide for writing adapter contract tests in this project. Use when creating or reviewing tests for ports/adapters (SmoobuGateway, RequestMemory, ReservationCache, etc.)
---

# Adapter Contract Testing

## What it is

A technique to keep simulators honest. The same abstract test class is run against **both** the real adapter and the simulator, proving they behave identically. If the real API changes or a simulator drifts, a test fails.

See: https://github.com/adapter-contract-testing/adapter-contract-testing-papers

## The pattern (canonical structure)

```python
# contracts/foo_contract.py  — the shared spec
class FooContract(ABC):
    @abstractmethod
    def create_foo(self) -> Foo: ...   # only one abstract method

    def test_something(self):
        foo = self.create_foo()
        ...

# test_foo.py  — one class per implementation, nothing else
class TestRealFoo(FooContract):
    def create_foo(self): return RealFoo(...)

class TestSimulatorFoo(FooContract):
    def create_foo(self): return SimulatorFoo(...)
```

## Rules

1. **One abstract method** — `create_foo()` (or `create_gateway()` etc.). No other setup plumbing.

2. **Two concrete subclasses** — real adapter + simulator. One file per port, not spread across multiple files.

3. **No `pytest.skip` in subclasses** — if a test needs pre-existing data, `create_foo()` must return a pre-seeded instance. A skip means the subclass doesn't honour the contract.

4. **No simulator-only test classes** — simulator-specific behaviour (internal state, edge cases unique to the fake) goes in standalone `test_` functions in the same file, not in a third contract subclass.

5. **The contract IS the port spec** — every behaviour the pipeline relies on must have a test here. If you can't write a contract test for it, question whether it belongs on the port at all.

## Example from this codebase

```python
# tests/contracts/smoobu_gateway_contract.py
class SmoobuGatewayContract(ABC):
    @abstractmethod
    def create_gateway(self) -> SmoobuGateway: ...

    def test_get_threads_non_empty(self):
        page = self.create_gateway().get_threads(1)
        assert len(page.threads) > 0

# tests/test_smoobu_contract.py
class TestSimulatorSmoobuContract(SmoobuGatewayContract):
    def create_gateway(self):
        gw = SimulatorSmoobuGateway()
        # Pre-seed so ALL contract tests pass without skips
        gw.inject_active_reservation(...)
        gw.inject_guest_message(...)
        return gw

class TestSmoobuClientContract(SmoobuGatewayContract):
    @pytest.mark.skipif(not API_KEY, reason="no credentials")
    def create_gateway(self): return SmoobuClient(API_KEY)
```

## What goes where

| Location | Content |
|---|---|
| `tests/contracts/<port>_contract.py` | Abstract contract class, all tests |
| `tests/test_<port>.py` | Two concrete subclasses (real + simulator); standalone simulator-specific tests as plain functions |
