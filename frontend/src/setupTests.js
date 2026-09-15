import "@testing-library/jest-dom";

// jsdom doesn't implement ResizeObserver; recharts' <ResponsiveContainer>
// needs one to mount at all, so without this every chart-bearing page
// throws during render in tests (the container itself never uses layout).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
global.ResizeObserver = global.ResizeObserver || ResizeObserverStub;
