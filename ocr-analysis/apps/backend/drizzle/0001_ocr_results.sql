CREATE TABLE "OCRContractDetail" (
	"id" integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "OCRContractDetail_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1),
	"documentKey" varchar(64) NOT NULL,
	"filePath" varchar(1000) NOT NULL,
	"status" varchar(20) NOT NULL,
	"seconds" real,
	"vendor" varchar(255),
	"entity" varchar(255),
	"employeeName" varchar(255),
	"nik" varchar(16),
	"position" varchar(100),
	"startDate" date,
	"endDate" date,
	"months" integer,
	"signature" varchar(10),
	"checks" varchar,
	"reasons" varchar,
	"headerId" integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE "OCRHeader" (
	"id" integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY (sequence name "OCRHeader_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1),
	"status" varchar(20) NOT NULL,
	"folders" varchar NOT NULL,
	"totalFiles" integer NOT NULL,
	"inputTokens" integer DEFAULT 0 NOT NULL,
	"cacheWriteTokens" integer DEFAULT 0 NOT NULL,
	"cacheReadTokens" integer DEFAULT 0 NOT NULL,
	"outputTokens" integer DEFAULT 0 NOT NULL,
	"startedAt" timestamp with time zone NOT NULL,
	"finishedAt" timestamp with time zone,
	"userId" integer NOT NULL
);
--> statement-breakpoint
ALTER TABLE "OCRContractDetail" ADD CONSTRAINT "OCRContractDetail_headerId_OCRHeader_id_fk" FOREIGN KEY ("headerId") REFERENCES "public"."OCRHeader"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "OCRHeader" ADD CONSTRAINT "OCRHeader_userId_User_id_fk" FOREIGN KEY ("userId") REFERENCES "public"."User"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "OCRContractDetail_headerId_documentKey" ON "OCRContractDetail" USING btree ("headerId","documentKey");--> statement-breakpoint
CREATE INDEX "OCRContractDetail_documentKey" ON "OCRContractDetail" USING btree ("documentKey");