export interface Bin {
  lo: number;
  hi: number;
  label: string;   // would be something like "$20K–$60K"
}

export interface PriorBelief {
  datasetId: string;
  attribute: string;
  binEdges: number[];   // length = binCount + 1
  counts: number[];     // length = binCount, sums to ballCount
  ballCount: number;
  columnHash: string;   // invalidate if column data changes
  createdAt: number;
}

export type PriorBeliefKey = `${string}::${string}`; 
