-- seconds is always recorded to 0.1 s; a float4 read back as 15.699999809, so rows saved before this round to 0.1 s
ALTER TABLE "OCRContractDetail" ALTER COLUMN "seconds" SET DATA TYPE double precision USING round("seconds"::numeric, 1)::double precision;
